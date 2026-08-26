from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)


def _truthy(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def default_cookie_file() -> Path:
    state_home = os.getenv("XDG_STATE_HOME")
    root = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return root / "onepoint3acres" / "cookies.json"


def default_pending_directory() -> Path:
    state_home = os.getenv("XDG_STATE_HOME")
    root = Path(state_home) if state_home else Path.home() / ".local" / "state"
    return root / "onepoint3acres" / "pending-questions"


@dataclass(frozen=True)
class Settings:
    username: str | None
    password: str | None
    initial_cookie: str | None
    two_captcha_key: str | None
    cookie_file: Path
    pending_directory: Path
    question_bank_file: Path | None
    user_agent: str = DEFAULT_USER_AGENT
    request_timeout: float = 20.0
    dry_run: bool = False

    @classmethod
    def from_env(cls) -> Settings:
        bank = os.getenv("ONEPOINT3ACRES_QUESTION_BANK_FILE")
        return cls(
            username=os.getenv("ONEPOINT3ACRES_USERNAME") or None,
            password=os.getenv("ONEPOINT3ACRES_PASSWORD") or None,
            initial_cookie=os.getenv("ONEPOINT3ACRES_COOKIE") or None,
            two_captcha_key=os.getenv("TWO_CAPTCHA_API_KEY") or None,
            cookie_file=Path(
                os.getenv("ONEPOINT3ACRES_COOKIE_FILE", str(default_cookie_file()))
            ).expanduser(),
            pending_directory=Path(
                os.getenv("ONEPOINT3ACRES_PENDING_DIRECTORY", str(default_pending_directory()))
            ).expanduser(),
            question_bank_file=Path(bank).expanduser() if bank else None,
            user_agent=os.getenv("ONEPOINT3ACRES_USER_AGENT") or DEFAULT_USER_AGENT,
            request_timeout=float(os.getenv("ONEPOINT3ACRES_REQUEST_TIMEOUT", "20")),
            dry_run=_truthy(os.getenv("ONEPOINT3ACRES_DRY_RUN")),
        )

    @property
    def account_label(self) -> str:
        if not self.username:
            return "cookie-account"
        if len(self.username) <= 4:
            return self.username[0] + "***"
        return f"{self.username[:2]}***{self.username[-2:]}"

    def validate(self, *, require_captcha: bool = False) -> list[str]:
        errors: list[str] = []
        if not self.user_agent.strip():
            errors.append("ONEPOINT3ACRES_USER_AGENT must not be empty")
        if self.request_timeout <= 0:
            errors.append("ONEPOINT3ACRES_REQUEST_TIMEOUT must be positive")
        if bool(self.username) != bool(self.password):
            errors.append("username and password must be configured together")
        if require_captcha and not self.two_captcha_key:
            errors.append("TWO_CAPTCHA_API_KEY is required for live submissions")
        if not self.initial_cookie and not self.cookie_file.exists() and not self.username:
            errors.append("configure a cookie or username/password")
        return errors
