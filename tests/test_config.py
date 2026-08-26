from __future__ import annotations

from pathlib import Path

from onepoint3acres.config import DEFAULT_USER_AGENT, Settings


def test_configuration_requires_complete_credentials(tmp_path: Path) -> None:
    settings = Settings(
        username="user",
        password=None,
        initial_cookie=None,
        two_captcha_key=None,
        cookie_file=tmp_path / "missing.json",
        pending_directory=tmp_path / "pending",
        question_bank_file=None,
    )

    assert "username and password must be configured together" in settings.validate()


def test_live_configuration_requires_captcha_key(settings: Settings) -> None:
    assert settings.validate(require_captcha=True) == [
        "TWO_CAPTCHA_API_KEY is required for live submissions"
    ]


def test_account_label_masks_username(settings: Settings) -> None:
    configured = Settings(**{**settings.__dict__, "username": "someone@example.com"})
    assert configured.account_label == "so***om"


def test_default_user_agent_matches_browser_cookie_platform(settings: Settings) -> None:
    assert settings.user_agent == DEFAULT_USER_AGENT
    assert "Macintosh" in settings.user_agent


def test_empty_user_agent_is_rejected(settings: Settings) -> None:
    configured = Settings(**{**settings.__dict__, "user_agent": " "})
    assert "ONEPOINT3ACRES_USER_AGENT must not be empty" in configured.validate()
