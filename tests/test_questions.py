from __future__ import annotations

import json
from pathlib import Path

from onepoint3acres.models import DailyQuestion, QuestionResolutionStatus
from onepoint3acres.question_bank import QuestionBank, QuestionEntry
from onepoint3acres.reports import QuestionReportStore


def _bank(tmp_path: Path) -> QuestionBank:
    return QuestionBank(
        [QuestionEntry("Example question?", ("Example question？",), ("Correct answer",))],
        source=tmp_path / "bank.json",
    )


def test_question_resolution_matches_answer_text_not_position(tmp_path: Path) -> None:
    question = DailyQuestion(
        7,
        "Example question？",
        {1: "Wrong", 2: "Correct answer", 3: "Other", 4: "No"},
    )
    resolution = _bank(tmp_path).resolve(question)
    assert resolution.status is QuestionResolutionStatus.MATCHED
    assert resolution.answer_index == 2


def test_unknown_question_report_is_deduplicated(tmp_path: Path) -> None:
    question = DailyQuestion(9, "New question", {1: "A", 2: "B", 3: "C", 4: "D"})
    resolution = _bank(tmp_path).resolve(question)
    store = QuestionReportStore(tmp_path / "pending")

    first = store.record(question, resolution)
    second = store.record(question, resolution)
    payload = json.loads(second.read_text(encoding="utf-8"))

    assert first == second
    assert payload["status"] == "unknown_question"
    assert payload["occurrences"] == 2
    assert "cookie" not in payload


def test_human_approval_adds_answer_to_bank(tmp_path: Path) -> None:
    question = DailyQuestion(9, "New question", {1: "A", 2: "B", 3: "C", 4: "D"})
    bank = _bank(tmp_path)
    report = QuestionReportStore(tmp_path / "pending").record(question, bank.resolve(question))
    output = tmp_path / "bank.json"

    bank.approve_report(report, answer_index=3, output=output)
    updated = QuestionBank.load(output)

    assert updated.resolve(question).answer_index == 3
