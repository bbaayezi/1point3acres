from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AuthStatus(StrEnum):
    SUCCESS = "success"
    INVALID_COOKIE = "invalid_cookie"
    INVALID_CREDENTIALS = "invalid_credentials"
    INTERACTIVE_LOGIN_REQUIRED = "interactive_login_required"
    CHALLENGE_BLOCKED = "challenge_blocked"
    CONFIGURATION_ERROR = "configuration_error"
    NETWORK_ERROR = "network_error"


class OperationStatus(StrEnum):
    SUCCESS = "success"
    ALREADY_DONE = "already_done"
    DRY_RUN = "dry_run"
    REVIEW_REQUIRED = "review_required"
    AUTH_REQUIRED = "auth_required"
    CHALLENGE_BLOCKED = "challenge_blocked"
    FAILED = "failed"
    SKIPPED = "skipped"


class QuestionResolutionStatus(StrEnum):
    MATCHED = "matched"
    UNKNOWN = "unknown_question"
    ANSWER_NOT_PRESENT = "answer_not_present"
    AMBIGUOUS = "ambiguous_answer"
    ANSWER_REJECTED = "answer_rejected"


@dataclass(frozen=True)
class AuthResult:
    status: AuthStatus
    message: str

    @property
    def ok(self) -> bool:
        return self.status is AuthStatus.SUCCESS


@dataclass(frozen=True)
class OperationResult:
    status: OperationStatus
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in {
            OperationStatus.SUCCESS,
            OperationStatus.ALREADY_DONE,
            OperationStatus.DRY_RUN,
            OperationStatus.SKIPPED,
        }


@dataclass(frozen=True)
class CaptchaSolution:
    task_id: str
    code: str


@dataclass(frozen=True)
class DailyQuestion:
    question_id: int
    text: str
    options: dict[int, str]


@dataclass(frozen=True)
class QuestionResolution:
    status: QuestionResolutionStatus
    answer_index: int | None
    expected_answers: tuple[str, ...]
    message: str


@dataclass(frozen=True)
class DailyRunResult:
    account: str
    auth: AuthResult
    checkin: OperationResult
    question: OperationResult

    @property
    def exit_code(self) -> int:
        statuses = {self.checkin.status, self.question.status}
        if not self.auth.ok or OperationStatus.AUTH_REQUIRED in statuses:
            return 2
        if OperationStatus.REVIEW_REQUIRED in statuses:
            return 3
        if OperationStatus.CHALLENGE_BLOCKED in statuses:
            return 4
        if any(not result.ok for result in (self.checkin, self.question)):
            return 1
        return 0
