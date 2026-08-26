from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

from requests.cookies import RequestsCookieJar, create_cookie

_COOKIE_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


class CookieStoreError(ValueError):
    pass


class CookieStore:
    """Persist a requests cookie jar without ever logging cookie values."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @staticmethod
    def import_header(header: str) -> RequestsCookieJar:
        if "\r" in header or "\n" in header:
            raise CookieStoreError("cookie header contains a line break")
        pairs: list[tuple[str, str]] = []
        for raw_part in header.split(";"):
            part = raw_part.strip()
            if not part:
                continue
            name, separator, value = part.partition("=")
            name = name.strip()
            if not separator or not _COOKIE_NAME.fullmatch(name):
                raise CookieStoreError("cookie header is malformed")
            pairs.append((name, value.strip()))
        if not pairs:
            raise CookieStoreError("cookie header contained no cookies")
        jar = RequestsCookieJar()
        for name, value in pairs:
            jar.set_cookie(create_cookie(name=name, value=value))  # type: ignore[no-untyped-call]
        return jar

    def load(self) -> RequestsCookieJar:
        jar = RequestsCookieJar()
        if not self.path.exists():
            return jar
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            records = payload["cookies"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise CookieStoreError(f"cannot read cookie store: {self.path}") from exc
        for record in records:
            jar.set_cookie(
                create_cookie(  # type: ignore[no-untyped-call]
                    name=record["name"],
                    value=record["value"],
                    domain=record.get("domain", ""),
                    path=record.get("path", "/"),
                    secure=bool(record.get("secure", False)),
                    expires=record.get("expires"),
                    rest=record.get("rest", {}),
                )
            )
        return jar

    @staticmethod
    def _records(jar: RequestsCookieJar) -> list[dict[str, Any]]:
        return sorted(
            (
                {
                    "name": cookie.name,
                    "value": cookie.value,
                    "domain": cookie.domain,
                    "path": cookie.path or "/",
                    "secure": cookie.secure,
                    "expires": cookie.expires,
                    "rest": dict(cookie._rest),  # type: ignore[attr-defined]
                }
                for cookie in jar
            ),
            key=lambda item: (item["domain"], item["path"], item["name"]),
        )

    def save(self, jar: RequestsCookieJar) -> bool:
        records = self._records(jar)
        if not records:
            raise CookieStoreError("refusing to persist an empty cookie jar")
        payload = {"version": 1, "cookies": records}
        serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if self.path.exists() and self.path.read_text(encoding="utf-8") == serialized:
            return False

        parent_existed = self.path.parent.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.path.parent, 0o700)
        except PermissionError:
            # A caller may deliberately place a mode-0600 file in an existing
            # system-owned directory such as /tmp. Never weaken a directory we
            # create, but do not require ownership of a pre-existing parent.
            if not parent_existed:
                raise
        if self.path.exists():
            backup = self.path.with_suffix(self.path.suffix + ".bak")
            shutil.copy2(self.path, backup)
            os.chmod(backup, 0o600)

        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=self.path.parent, text=True
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
        return True

    def clear(self) -> bool:
        if not self.path.exists():
            return False
        self.path.unlink()
        return True
