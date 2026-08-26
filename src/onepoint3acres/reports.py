from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import DailyQuestion, QuestionResolution
from .question_bank import normalize_text


class QuestionReportStore:
    def __init__(self, directory: Path) -> None:
        self.directory = directory

    @staticmethod
    def fingerprint(question: DailyQuestion) -> str:
        material = {
            "question": normalize_text(question.text),
            "options": {
                str(index): normalize_text(value)
                for index, value in sorted(question.options.items())
            },
        }
        encoded = json.dumps(material, ensure_ascii=False, sort_keys=True).encode()
        return hashlib.sha256(encoded).hexdigest()

    def record(
        self,
        question: DailyQuestion,
        resolution: QuestionResolution,
        *,
        response_message: str | None = None,
    ) -> Path:
        fingerprint = self.fingerprint(question)
        path = self.directory / f"{fingerprint}.json"
        now = datetime.now(UTC).isoformat()
        occurrences = 1
        first_seen = now
        if path.exists():
            try:
                previous = json.loads(path.read_text(encoding="utf-8"))
                occurrences = int(previous.get("occurrences", 0)) + 1
                first_seen = str(previous.get("first_seen", now))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                pass
        payload: dict[str, Any] = {
            "version": 1,
            "fingerprint": fingerprint,
            "status": resolution.status.value,
            "question_id": question.question_id,
            "question": question.text,
            "options": {str(index): value for index, value in sorted(question.options.items())},
            "expected_answers": list(resolution.expected_answers),
            "first_seen": first_seen,
            "last_seen": now,
            "occurrences": occurrences,
        }
        if response_message:
            payload["response_message"] = response_message[:500]
        self._write(path, payload)
        return path

    def list_reports(self) -> list[Path]:
        if not self.directory.exists():
            return []
        return sorted(self.directory.glob("*.json"))

    def _write(self, path: Path, payload: dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=self.directory, text=True
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
