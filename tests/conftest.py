from __future__ import annotations

from pathlib import Path

import pytest

from onepoint3acres.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        username=None,
        password=None,
        initial_cookie="session=seed",
        two_captcha_key=None,
        cookie_file=tmp_path / "cookies.json",
        pending_directory=tmp_path / "pending",
        question_bank_file=None,
        request_timeout=1,
        dry_run=True,
    )
