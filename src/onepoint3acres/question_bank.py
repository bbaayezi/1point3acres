from __future__ import annotations

import json
import os
import tempfile
import unicodedata
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from .models import DailyQuestion, QuestionResolution, QuestionResolutionStatus


class QuestionBankError(ValueError):
    pass


def normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).strip()
    normalized = " ".join(normalized.split())
    return normalized.rstrip("?？").strip()


@dataclass(frozen=True)
class QuestionEntry:
    question: str
    accepted_variants: tuple[str, ...]
    answers: tuple[str, ...]
    status: str = "approved"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> QuestionEntry:
        question = payload.get("question")
        answers = payload.get("answers")
        if not isinstance(question, str) or not question.strip():
            raise QuestionBankError("question-bank entry has no question")
        if (
            not isinstance(answers, list)
            or not answers
            or not all(isinstance(answer, str) and answer.strip() for answer in answers)
        ):
            raise QuestionBankError(f"question-bank entry has invalid answers: {question}")
        variants = payload.get("accepted_variants", [])
        if not isinstance(variants, list) or not all(isinstance(item, str) for item in variants):
            raise QuestionBankError(f"question-bank entry has invalid variants: {question}")
        return cls(
            question=question,
            accepted_variants=tuple(variants),
            answers=tuple(answers),
            status=str(payload.get("status", "approved")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "accepted_variants": list(self.accepted_variants),
            "answers": list(self.answers),
            "status": self.status,
        }


def bundled_bank_path() -> Path:
    return Path(str(files("onepoint3acres").joinpath("question_bank.json")))


class QuestionBank:
    def __init__(self, entries: list[QuestionEntry], *, source: Path) -> None:
        self.entries = entries
        self.source = source
        self._index: dict[str, list[QuestionEntry]] = {}
        for entry in entries:
            for text in (entry.question, *entry.accepted_variants):
                self._index.setdefault(normalize_text(text), []).append(entry)

    @classmethod
    def load(cls, path: Path | None = None) -> QuestionBank:
        source = path or bundled_bank_path()
        try:
            payload = json.loads(source.read_text(encoding="utf-8"))
            raw_entries = payload["questions"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise QuestionBankError(f"cannot load question bank: {source}") from exc
        if payload.get("version") != 1 or not isinstance(raw_entries, list):
            raise QuestionBankError("unsupported question-bank schema")
        entries = [QuestionEntry.from_dict(item) for item in raw_entries]
        bank = cls(entries, source=source)
        bank.validate()
        return bank

    def validate(self) -> None:
        conflicts = {
            key: entries
            for key, entries in self._index.items()
            if len({entry.answers for entry in entries if entry.status == "approved"}) > 1
        }
        if conflicts:
            examples = ", ".join(sorted(conflicts)[:3])
            raise QuestionBankError(f"conflicting normalized questions: {examples}")

    def resolve(self, question: DailyQuestion) -> QuestionResolution:
        candidates = [
            entry
            for entry in self._index.get(normalize_text(question.text), [])
            if entry.status == "approved"
        ]
        if not candidates:
            return QuestionResolution(
                QuestionResolutionStatus.UNKNOWN,
                None,
                (),
                "question is not present in the approved bank",
            )
        expected = tuple(dict.fromkeys(answer for item in candidates for answer in item.answers))
        matches = [
            index
            for index, option in question.options.items()
            if normalize_text(option) in {normalize_text(answer) for answer in expected}
        ]
        if not matches:
            return QuestionResolution(
                QuestionResolutionStatus.ANSWER_NOT_PRESENT,
                None,
                expected,
                "known answer is not present in the current options",
            )
        if len(matches) > 1:
            return QuestionResolution(
                QuestionResolutionStatus.AMBIGUOUS,
                None,
                expected,
                "multiple current options match approved answers",
            )
        return QuestionResolution(
            QuestionResolutionStatus.MATCHED,
            matches[0],
            expected,
            "approved answer matched exactly",
        )

    def approve_report(self, report_path: Path, *, answer_index: int, output: Path) -> None:
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
            question = str(report["question"])
            answer = str(report["options"][str(answer_index)])
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise QuestionBankError(f"cannot approve report: {report_path}") from exc

        existing = next(
            (
                entry
                for entry in self.entries
                if normalize_text(entry.question) == normalize_text(question)
            ),
            None,
        )
        updated = list(self.entries)
        if existing:
            replacement = QuestionEntry(
                question=existing.question,
                accepted_variants=existing.accepted_variants,
                answers=tuple(dict.fromkeys((*existing.answers, answer))),
                status="approved",
            )
            updated[updated.index(existing)] = replacement
        else:
            updated.append(QuestionEntry(question, (), (answer,), "approved"))
        updated.sort(key=lambda entry: normalize_text(entry.question))
        QuestionBank(updated, source=output).validate()
        self._write(output, updated)

    @staticmethod
    def _write(path: Path, entries: list[QuestionEntry]) -> None:
        payload = {
            "version": 1,
            "questions": [entry.to_dict() for entry in entries],
        }
        serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", dir=path.parent, text=True
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()
