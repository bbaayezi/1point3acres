from __future__ import annotations

import os

import pytest
import requests

from onepoint3acres.client import AUTH_URL, DAILY_QUESTION_API, USER_AGENT, parse_login_form

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("RUN_LIVE_CONTRACT") != "1",
        reason="set RUN_LIVE_CONTRACT=1 to contact public endpoints",
    ),
]


def test_public_login_form_contract() -> None:
    response = requests.get(AUTH_URL, headers={"User-Agent": USER_AGENT}, timeout=20)
    form = parse_login_form(response.text)

    assert response.status_code == 200
    assert form.hidden_fields.get("csrf_token")
    assert form.uses_turnstile
    assert form.sitekey


def test_daily_question_api_requires_authentication() -> None:
    response = requests.get(DAILY_QUESTION_API, headers={"User-Agent": USER_AGENT}, timeout=20)
    assert response.status_code in {401, 403}
