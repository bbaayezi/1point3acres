from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from onepoint3acres.cookies import CookieStore, CookieStoreError


def test_cookie_header_round_trip_and_permissions(tmp_path: Path) -> None:
    store = CookieStore(tmp_path / "state" / "cookies.json")
    jar = store.import_header("session=abc; preference=compact")

    assert store.save(jar) is True
    loaded = store.load()

    assert loaded.get("session") == "abc"
    assert loaded.get("preference") == "compact"
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert stat.S_IMODE(store.path.parent.stat().st_mode) == 0o700
    assert store.save(loaded) is False


def test_refresh_keeps_one_backup(tmp_path: Path) -> None:
    store = CookieStore(tmp_path / "cookies.json")
    store.save(store.import_header("session=old"))
    store.save(store.import_header("session=new"))

    backup = store.path.with_suffix(".json.bak")
    assert backup.exists()
    assert CookieStore(backup).load().get("session") == "old"
    assert store.load().get("session") == "new"


def test_existing_system_owned_parent_does_not_block_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = CookieStore(tmp_path / "cookies.json")
    real_chmod = os.chmod

    def protected_parent_chmod(path: os.PathLike[str] | str, mode: int) -> None:
        if Path(path) == tmp_path:
            raise PermissionError("system-owned parent")
        real_chmod(path, mode)

    monkeypatch.setattr(os, "chmod", protected_parent_chmod)

    assert store.save(store.import_header("session=abc")) is True
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_empty_cookie_is_rejected() -> None:
    with pytest.raises(CookieStoreError):
        CookieStore.import_header("")


def test_browser_cookie_values_are_preserved_verbatim() -> None:
    jar = CookieStore.import_header(
        "cf_clearance=abc:def@ghi; session=a%2Fb%3Dc; analytics=one|two"
    )

    assert jar.get("cf_clearance") == "abc:def@ghi"
    assert jar.get("session") == "a%2Fb%3Dc"
    assert jar.get("analytics") == "one|two"


@pytest.mark.parametrize("header", ["Cookie: session=abc", "session=abc\r\nInjected: yes"])
def test_malformed_or_multiline_cookie_is_rejected(header: str) -> None:
    with pytest.raises(CookieStoreError):
        CookieStore.import_header(header)
