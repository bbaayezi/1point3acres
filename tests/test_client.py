from __future__ import annotations

from dataclasses import replace

import responses

from onepoint3acres.captcha import CaptchaSolution
from onepoint3acres.client import (
    AUTH_LOGIN_URL,
    AUTH_URL,
    DAILY_QUESTION_API,
    SiteClient,
    is_cloudflare_challenge,
    parse_login_form,
)
from onepoint3acres.models import AuthStatus

LOGIN_HTML = """
<form action="/login" method="post">
  <input name="csrf_token" type="hidden" value="csrf-value">
  <script src="https://challenges.cloudflare.com/turnstile/v0/api.js?compat=recaptcha"></script>
  <div class="g-recaptcha" data-sitekey="0x4CURRENT"></div>
</form>
"""


class FakeSolver:
    def __init__(self) -> None:
        self.solves = 0
        self.reports: list[tuple[str, bool]] = []

    def solve_turnstile(self, *, sitekey: str, url: str, user_agent: str) -> CaptchaSolution:
        assert sitekey == "0x4CURRENT"
        assert url == AUTH_URL
        assert user_agent
        self.solves += 1
        return CaptchaSolution("task-1", "solution")

    def report(self, task_id: str, *, correct: bool) -> None:
        self.reports.append((task_id, correct))


def test_login_form_is_discovered_dynamically() -> None:
    form = parse_login_form(LOGIN_HTML)
    assert form.action == AUTH_LOGIN_URL
    assert form.hidden_fields["csrf_token"] == "csrf-value"
    assert form.sitekey == "0x4CURRENT"
    assert form.uses_turnstile is True


def test_cloudflare_managed_challenge_is_detected() -> None:
    import requests

    response = requests.Response()
    response.status_code = 200
    response._content = (
        b"<title>Just a moment...</title><script src='/cdn-cgi/challenge-platform/x'>"
    )
    assert is_cloudflare_challenge(response)


@responses.activate
def test_valid_cookie_is_persisted(settings) -> None:  # type: ignore[no-untyped-def]
    responses.get(DAILY_QUESTION_API, json={"errno": 0, "msg": "OK", "question": {}})
    client = SiteClient(settings, FakeSolver())

    result = client.authenticate()

    assert result.status is AuthStatus.SUCCESS
    assert settings.cookie_file.exists()


@responses.activate
def test_failed_password_path_prompts_for_cookie(settings) -> None:  # type: ignore[no-untyped-def]
    responses.get(DAILY_QUESTION_API, status=401)
    responses.get(DAILY_QUESTION_API, json={"errno": 0, "msg": "OK", "question": {}})
    client = SiteClient(settings, FakeSolver())

    result = client.authenticate(cookie_prompt=lambda reason: "session=fresh")

    assert result.status is AuthStatus.SUCCESS
    assert client.session.cookies.get("session") == "fresh"


@responses.activate
def test_transient_cookie_validation_does_not_attempt_password(settings) -> None:  # type: ignore[no-untyped-def]
    configured = replace(settings, username="user", password="pass")
    responses.get(DAILY_QUESTION_API, status=503)
    client = SiteClient(configured, FakeSolver())

    result = client.authenticate(cookie_prompt=lambda reason: "session=fresh")

    assert result.status is AuthStatus.NETWORK_ERROR
    assert responses.calls
    assert {call.request.url for call in responses.calls} == {DAILY_QUESTION_API}


@responses.activate
def test_password_login_uses_turnstile_and_validates_session(settings) -> None:  # type: ignore[no-untyped-def]
    configured = replace(settings, initial_cookie=None, username="user", password="pass")
    responses.get(AUTH_URL, body=LOGIN_HTML)
    responses.post(AUTH_LOGIN_URL, status=302, headers={"Set-Cookie": "session=logged-in"})
    responses.get(DAILY_QUESTION_API, json={"errno": 0, "msg": "OK", "question": {}})
    solver = FakeSolver()
    client = SiteClient(configured, solver)

    result = client.password_login()

    assert result.status is AuthStatus.SUCCESS
    assert solver.solves == 1
    assert solver.reports == [("task-1", True)]
