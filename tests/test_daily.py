from __future__ import annotations

import json
from pathlib import Path

import requests

from onepoint3acres.captcha import CaptchaSolution
from onepoint3acres.config import Settings
from onepoint3acres.daily import DailyRunner
from onepoint3acres.models import AuthResult, AuthStatus, OperationStatus
from onepoint3acres.question_bank import QuestionBank, QuestionEntry
from onepoint3acres.reports import QuestionReportStore


def _response(status: int, payload: object | None = None, body: str = "") -> requests.Response:
    response = requests.Response()
    response.status_code = status
    if payload is not None:
        response._content = json.dumps(payload, ensure_ascii=False).encode()
        response.headers["Content-Type"] = "application/json"
    else:
        response._content = body.encode()
    return response


class FakeSolver:
    def __init__(self) -> None:
        self.solves = 0

    def solve_turnstile(self, *, sitekey: str, url: str, user_agent: str) -> CaptchaSolution:
        del sitekey, url, user_agent
        self.solves += 1
        return CaptchaSolution("task", "code")

    def report(self, task_id: str, *, correct: bool) -> None:
        del task_id, correct


class FakeClient:
    def __init__(self, settings: Settings, responses: list[requests.Response]) -> None:
        self.settings = settings
        self.responses = responses

    def _request(self, method: str, url: str, **kwargs: object) -> requests.Response:
        del method, url, kwargs
        return self.responses.pop(0)

    def persist_cookies(self) -> bool:
        return False


def _question_payload(text: str = "Unknown") -> dict:
    return {
        "errno": 0,
        "msg": "OK",
        "question": {"id": 1, "qc": text, "a1": "A", "a2": "B", "a3": "C", "a4": "D"},
    }


def _status_payload(*, question: bool = False, checkin: bool = False) -> list[dict]:
    return [
        {"result": {"data": {"json": {"app_status": {"question": question, "checkIn": checkin}}}}}
    ]


def test_unknown_question_does_not_solve_captcha(settings: Settings) -> None:
    solver = FakeSolver()
    client = FakeClient(
        settings, [_response(200, _status_payload()), _response(200, _question_payload())]
    )
    bank = QuestionBank([], source=Path("bank.json"))
    runner = DailyRunner(client, solver, bank, QuestionReportStore(settings.pending_directory))  # type: ignore[arg-type]

    result = runner.run_question(dry_run=False)

    assert result.status is OperationStatus.REVIEW_REQUIRED
    assert solver.solves == 0
    assert Path(result.details["report"]).exists()


def test_dry_run_resolves_without_submission(settings: Settings) -> None:
    solver = FakeSolver()
    client = FakeClient(
        settings,
        [_response(200, _status_payload()), _response(200, _question_payload("Known"))],
    )
    bank = QuestionBank([QuestionEntry("Known", (), ("C",))], source=Path("bank.json"))
    runner = DailyRunner(client, solver, bank, QuestionReportStore(settings.pending_directory))  # type: ignore[arg-type]

    result = runner.run_question(dry_run=True)

    assert result.status is OperationStatus.DRY_RUN
    assert result.details["answer_index"] == 3
    assert solver.solves == 0


def test_successful_question_is_submitted_and_verified(settings: Settings) -> None:
    solver = FakeSolver()
    client = FakeClient(
        settings,
        [
            _response(200, _status_payload()),
            _response(200, _question_payload("Known")),
            _response(200, {"errno": 0, "msg": "回答正确"}),
        ],
    )
    bank = QuestionBank([QuestionEntry("Known", (), ("C",))], source=Path("bank.json"))
    runner = DailyRunner(client, solver, bank, QuestionReportStore(settings.pending_directory))  # type: ignore[arg-type]

    result = runner.run_question(dry_run=False)

    assert result.status is OperationStatus.SUCCESS
    assert solver.solves == 1


def test_rejected_approved_answer_creates_review_report(settings: Settings) -> None:
    solver = FakeSolver()
    client = FakeClient(
        settings,
        [
            _response(200, _status_payload()),
            _response(200, _question_payload("Known")),
            _response(200, {"errno": 7, "msg": "答案不正确"}),
        ],
    )
    bank = QuestionBank([QuestionEntry("Known", (), ("C",))], source=Path("bank.json"))
    runner = DailyRunner(client, solver, bank, QuestionReportStore(settings.pending_directory))  # type: ignore[arg-type]

    result = runner.run_question(dry_run=False)
    report = json.loads(Path(result.details["report"]).read_text(encoding="utf-8"))

    assert result.status is OperationStatus.REVIEW_REQUIRED
    assert report["status"] == "answer_rejected"


def test_already_checked_in_does_not_solve_captcha(settings: Settings) -> None:
    solver = FakeSolver()
    client = FakeClient(
        settings, [_response(200, _status_payload()), _response(200, body="今日已签到")]
    )
    runner = DailyRunner(
        client,  # type: ignore[arg-type]
        solver,
        QuestionBank([], source=Path("bank.json")),
        QuestionReportStore(settings.pending_directory),
    )

    result = runner.run_checkin(dry_run=False)

    assert result.status is OperationStatus.ALREADY_DONE
    assert solver.solves == 0


def test_managed_challenge_is_not_sent_to_solver(settings: Settings) -> None:
    solver = FakeSolver()
    challenge = _response(200, body="<title>Just a moment...</title>/cdn-cgi/challenge-platform/")
    client = FakeClient(settings, [challenge])
    runner = DailyRunner(
        client,  # type: ignore[arg-type]
        solver,
        QuestionBank([], source=Path("bank.json")),
        QuestionReportStore(settings.pending_directory),
    )

    result = runner.run_checkin(dry_run=False)

    assert result.status is OperationStatus.CHALLENGE_BLOCKED
    assert solver.solves == 0


def test_already_answered_status_avoids_a_paid_captcha(settings: Settings) -> None:
    solver = FakeSolver()
    client = FakeClient(settings, [_response(200, _status_payload(question=True))])
    runner = DailyRunner(
        client,  # type: ignore[arg-type]
        solver,
        QuestionBank([], source=Path("bank.json")),
        QuestionReportStore(settings.pending_directory),
    )

    result = runner.run_question(dry_run=False)

    assert result.status is OperationStatus.ALREADY_DONE
    assert solver.solves == 0


def test_auth_failure_has_nonzero_exit_code(settings: Settings) -> None:
    solver = FakeSolver()
    client = FakeClient(settings, [])
    runner = DailyRunner(
        client,  # type: ignore[arg-type]
        solver,
        QuestionBank([], source=Path("bank.json")),
        QuestionReportStore(settings.pending_directory),
    )
    auth = AuthResult(AuthStatus.INVALID_COOKIE, "expired")

    result = runner.run(auth)

    assert result.exit_code == 2
