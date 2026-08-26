from __future__ import annotations

import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .captcha import CaptchaError, CaptchaSolver
from .config import DEFAULT_USER_AGENT, Settings
from .cookies import CookieStore, CookieStoreError
from .models import AuthResult, AuthStatus

AUTH_URL = "https://auth.1point3acres.com/"
AUTH_LOGIN_URL = "https://auth.1point3acres.com/login"
DAILY_QUESTION_API = "https://api.1point3acres.com/api/daily_questions"
# Backwards-compatible export for contract checks and external callers.
USER_AGENT = DEFAULT_USER_AGENT


class SiteRequestError(RuntimeError):
    pass


def is_cloudflare_challenge(response: requests.Response) -> bool:
    body = response.text.lower()
    return "just a moment" in body or "/cdn-cgi/challenge-platform/" in body or "cf-chl-" in body


@dataclass(frozen=True)
class LoginForm:
    action: str
    hidden_fields: dict[str, str]
    sitekey: str | None
    uses_turnstile: bool


class _LoginFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_login_form = False
        self.action = AUTH_LOGIN_URL
        self.hidden_fields: dict[str, str] = {}
        self.sitekey: str | None = None
        self.uses_turnstile = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "form" and (values.get("method") or "").lower() == "post":
            self.in_login_form = True
            self.action = values.get("action") or AUTH_LOGIN_URL
        if tag == "script" and "turnstile" in (values.get("src") or "").lower():
            self.uses_turnstile = True
        if not self.in_login_form:
            return
        if tag == "input":
            name = values.get("name")
            if (values.get("type") or "").lower() == "hidden" and name:
                self.hidden_fields[name] = values.get("value") or ""
        sitekey = values.get("data-sitekey")
        if sitekey:
            self.sitekey = sitekey
            if sitekey.startswith("0x4"):
                self.uses_turnstile = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self.in_login_form:
            self.in_login_form = False


def parse_login_form(html: str, *, base_url: str = AUTH_URL) -> LoginForm:
    parser = _LoginFormParser()
    parser.feed(html)
    return LoginForm(
        action=urljoin(base_url, parser.action),
        hidden_fields=parser.hidden_fields,
        sitekey=parser.sitekey,
        uses_turnstile=parser.uses_turnstile,
    )


CookiePrompt = Callable[[str], str | None]


class SiteClient:
    def __init__(
        self,
        settings: Settings,
        captcha_solver: CaptchaSolver,
        *,
        session: requests.Session | None = None,
        cookie_store: CookieStore | None = None,
    ) -> None:
        self.settings = settings
        self.captcha_solver = captcha_solver
        self.session = session or requests.Session()
        self.cookie_store = cookie_store or CookieStore(settings.cookie_file)
        self.session.headers.update({"User-Agent": settings.user_agent, "Accept": "*/*"})
        retries = Retry(
            total=2,
            connect=2,
            read=2,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
        )
        self.session.mount("https://", HTTPAdapter(max_retries=retries))
        self._load_initial_cookies()

    def _load_initial_cookies(self) -> None:
        try:
            self.session.cookies.update(self.cookie_store.load())
            if self.settings.initial_cookie:
                self.session.cookies.update(CookieStore.import_header(self.settings.initial_cookie))
        except CookieStoreError as exc:
            raise SiteRequestError(str(exc)) from exc

    def _request(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.settings.request_timeout)
        try:
            return self.session.request(method, url, **kwargs)
        except requests.RequestException as exc:
            raise SiteRequestError(f"request failed for {url}: {type(exc).__name__}") from exc

    def persist_cookies(self) -> bool:
        return self.cookie_store.save(self.session.cookies)

    def _report_captcha(self, task_id: str, *, correct: bool) -> None:
        with suppress(CaptchaError):
            self.captcha_solver.report(task_id, correct=correct)

    def replace_with_cookie_header(self, cookie_header: str) -> None:
        jar = CookieStore.import_header(cookie_header)
        self.session.cookies.clear()
        self.session.cookies.update(jar)

    def validate_session(self) -> AuthResult:
        try:
            response = self._request(
                "GET", DAILY_QUESTION_API, headers={"Referer": "https://www.1point3acres.com/"}
            )
        except SiteRequestError as exc:
            return AuthResult(AuthStatus.NETWORK_ERROR, str(exc))
        if is_cloudflare_challenge(response):
            return AuthResult(
                AuthStatus.CHALLENGE_BLOCKED,
                "Cloudflare managed challenge blocked session validation",
            )
        if response.status_code in {401, 403}:
            return AuthResult(AuthStatus.INVALID_COOKIE, "session cookie is not authenticated")
        if response.status_code != 200:
            return AuthResult(
                AuthStatus.NETWORK_ERROR,
                f"session validation returned HTTP {response.status_code}",
            )
        try:
            payload = response.json()
        except requests.JSONDecodeError:
            return AuthResult(AuthStatus.INVALID_COOKIE, "session validation returned non-JSON")
        if not isinstance(payload, dict):
            return AuthResult(
                AuthStatus.INVALID_COOKIE, "session validation payload was unexpected"
            )
        message = str(payload.get("msg", ""))
        if re.search(r"登录|login|unauthorized", message, re.IGNORECASE):
            return AuthResult(AuthStatus.INVALID_COOKIE, "session is not logged in")
        return AuthResult(AuthStatus.SUCCESS, "authenticated session validated")

    def password_login(self) -> AuthResult:
        if not self.settings.username or not self.settings.password:
            return AuthResult(
                AuthStatus.CONFIGURATION_ERROR, "username/password are not configured"
            )
        try:
            page = self._request("GET", AUTH_URL)
        except SiteRequestError as exc:
            return AuthResult(AuthStatus.NETWORK_ERROR, str(exc))
        if is_cloudflare_challenge(page):
            return AuthResult(
                AuthStatus.CHALLENGE_BLOCKED,
                "authentication page returned a Cloudflare managed challenge",
            )
        if page.status_code != 200:
            return AuthResult(
                AuthStatus.NETWORK_ERROR,
                f"authentication page returned HTTP {page.status_code}",
            )
        form = parse_login_form(page.text)
        csrf_token = form.hidden_fields.get("csrf_token")
        if not csrf_token:
            return AuthResult(AuthStatus.NETWORK_ERROR, "login form has no CSRF token")
        if not form.sitekey or not form.uses_turnstile:
            return AuthResult(
                AuthStatus.CHALLENGE_BLOCKED,
                "login challenge type or site key is unsupported",
            )
        try:
            captcha = self.captcha_solver.solve_turnstile(
                sitekey=form.sitekey,
                url=AUTH_URL,
                user_agent=self.settings.user_agent,
            )
        except CaptchaError as exc:
            return AuthResult(AuthStatus.CHALLENGE_BLOCKED, str(exc))

        fields = dict(form.hidden_fields)
        fields.update(
            {
                "username": self.settings.username,
                "password": self.settings.password,
                "question_id": "0",
                "answer": "",
                "g-recaptcha-response": captcha.code,
                "cf-turnstile-response": captcha.code,
                "submit": "登录",
            }
        )
        try:
            response = self._request(
                "POST",
                form.action,
                data=fields,
                allow_redirects=False,
                headers={"Origin": "https://auth.1point3acres.com", "Referer": AUTH_URL},
            )
        except SiteRequestError as exc:
            return AuthResult(AuthStatus.NETWORK_ERROR, str(exc))

        body = response.text
        if "人机验证" in body or "验证码" in body:
            self._report_captcha(captcha.task_id, correct=False)
            return AuthResult(AuthStatus.CHALLENGE_BLOCKED, "login challenge was rejected")
        if re.search(r"用户名或密码错误|登录失败", body):
            return AuthResult(AuthStatus.INVALID_CREDENTIALS, "username or password was rejected")
        if "微信" in body and re.search(r"风险|扫码|登录", body):
            return AuthResult(
                AuthStatus.INTERACTIVE_LOGIN_REQUIRED,
                "account requires interactive WeChat login",
            )
        if response.status_code not in {200, 302, 303}:
            return AuthResult(
                AuthStatus.NETWORK_ERROR,
                f"login submission returned HTTP {response.status_code}",
            )

        validation = self.validate_session()
        if validation.ok:
            self._report_captcha(captcha.task_id, correct=True)
            self.persist_cookies()
            return AuthResult(AuthStatus.SUCCESS, "password login succeeded")
        return AuthResult(
            AuthStatus.INVALID_CREDENTIALS,
            f"login did not create an authenticated session: {validation.message}",
        )

    def authenticate(self, *, cookie_prompt: CookiePrompt | None = None) -> AuthResult:
        if self.session.cookies:
            validation = self.validate_session()
            if validation.ok:
                self.persist_cookies()
                return validation
            if validation.status in {AuthStatus.NETWORK_ERROR, AuthStatus.CHALLENGE_BLOCKED}:
                return validation

        password_result = self.password_login()
        if password_result.ok:
            return password_result

        if cookie_prompt is None:
            return AuthResult(
                AuthStatus.INTERACTIVE_LOGIN_REQUIRED,
                f"{password_result.message}; import a fresh browser cookie",
            )

        supplied = cookie_prompt(password_result.message)
        if not supplied:
            return AuthResult(
                AuthStatus.INTERACTIVE_LOGIN_REQUIRED,
                f"{password_result.message}; no replacement cookie was supplied",
            )
        try:
            self.replace_with_cookie_header(supplied)
        except CookieStoreError as exc:
            return AuthResult(AuthStatus.INVALID_COOKIE, str(exc))
        validation = self.validate_session()
        if validation.ok:
            self.persist_cookies()
            return AuthResult(AuthStatus.SUCCESS, "browser cookie login succeeded")
        return validation
