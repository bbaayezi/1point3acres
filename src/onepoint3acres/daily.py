from __future__ import annotations

import json
import random
import re
from contextlib import suppress

import requests

from .captcha import CaptchaError, CaptchaSolver
from .client import (
    DAILY_QUESTION_API,
    SiteClient,
    SiteRequestError,
    is_cloudflare_challenge,
)
from .models import (
    AuthResult,
    DailyQuestion,
    DailyRunResult,
    OperationResult,
    OperationStatus,
    QuestionResolution,
    QuestionResolutionStatus,
)
from .question_bank import QuestionBank
from .reports import QuestionReportStore

CHECKIN_PAGE = "https://www.1point3acres.com/next/daily-checkin"
CHECKIN_API = "https://api.1point3acres.com/api/users/checkin"
QUESTION_PAGE = "https://www.1point3acres.com/next/daily-question"
USER_STATUS_API = "https://trpc.1point3acres.com/trpc/user.me"
KNOWN_TURNSTILE_SITEKEY = "0x4AAAAAAAA6iSaNNPWafmlz"
CAPTCHA_ERROR_PATTERN = re.compile(r"人机验证|验证码.*(?:错误|出错|失败)")


def _json_payload(response: requests.Response) -> dict[str, object] | None:
    try:
        payload = response.json()
    except (requests.JSONDecodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


class DailyRunner:
    def __init__(
        self,
        client: SiteClient,
        captcha_solver: CaptchaSolver,
        question_bank: QuestionBank,
        report_store: QuestionReportStore,
    ) -> None:
        self.client = client
        self.captcha_solver = captcha_solver
        self.question_bank = question_bank
        self.report_store = report_store

    def fetch_daily_status(
        self,
    ) -> tuple[dict[str, bool] | None, OperationResult | None]:
        query = {"0": {"json": None}}
        try:
            response = self.client._request(
                "GET",
                USER_STATUS_API,
                params={"batch": "1", "input": json.dumps(query, separators=(",", ":"))},
                headers={"Referer": "https://www.1point3acres.com/"},
            )
        except SiteRequestError as exc:
            return None, OperationResult(OperationStatus.FAILED, str(exc))
        if is_cloudflare_challenge(response):
            return None, OperationResult(
                OperationStatus.CHALLENGE_BLOCKED,
                "daily status returned a Cloudflare managed edge challenge",
            )
        if response.status_code in {401, 403}:
            return None, OperationResult(
                OperationStatus.AUTH_REQUIRED, "daily status rejected the session"
            )
        if response.status_code != 200:
            return None, OperationResult(
                OperationStatus.FAILED,
                f"daily status returned HTTP {response.status_code}",
            )
        try:
            payload = response.json()
            data = payload[0]["result"]["data"]
            if not isinstance(data, dict):
                raise TypeError
            user = data["json"] if isinstance(data.get("json"), dict) else data
            if not isinstance(user, dict):
                raise TypeError
            app_status = user["app_status"]
            if not isinstance(app_status, dict):
                raise TypeError
        except (requests.JSONDecodeError, KeyError, IndexError, TypeError):
            return None, OperationResult(
                OperationStatus.FAILED, "daily status response schema changed"
            )
        return {
            "question": app_status.get("question") is True,
            "checkin": app_status.get("checkIn") is True,
        }, None

    def run(
        self,
        auth: AuthResult,
        *,
        dry_run: bool = False,
        checkin_only: bool = False,
        question_only: bool = False,
    ) -> DailyRunResult:
        if not auth.ok:
            unavailable = OperationResult(OperationStatus.AUTH_REQUIRED, auth.message)
            return DailyRunResult(
                self.client.settings.account_label, auth, unavailable, unavailable
            )
        checkin = (
            OperationResult(OperationStatus.SKIPPED, "check-in was not requested")
            if question_only
            else self.run_checkin(dry_run=dry_run)
        )
        question = (
            OperationResult(OperationStatus.SKIPPED, "daily question was not requested")
            if checkin_only
            else self.run_question(dry_run=dry_run)
        )
        # Authentication and operation results remain authoritative if persistence fails.
        with suppress(Exception):
            self.client.persist_cookies()
        return DailyRunResult(self.client.settings.account_label, auth, checkin, question)

    def run_checkin(self, *, dry_run: bool) -> OperationResult:
        daily_status, terminal = self.fetch_daily_status()
        if terminal:
            return terminal
        assert daily_status is not None
        if daily_status["checkin"]:
            return OperationResult(
                OperationStatus.ALREADY_DONE, "daily check-in is already complete"
            )
        try:
            page = self.client._request("GET", CHECKIN_PAGE)
        except SiteRequestError as exc:
            return OperationResult(OperationStatus.FAILED, str(exc))
        if is_cloudflare_challenge(page):
            return OperationResult(
                OperationStatus.CHALLENGE_BLOCKED,
                "check-in page returned a Cloudflare managed edge challenge",
            )
        if page.status_code in {401, 403} or re.search(r"请.*登录后.*签到", page.text):
            return OperationResult(
                OperationStatus.AUTH_REQUIRED, "check-in session is not authenticated"
            )
        if page.status_code != 200:
            return OperationResult(
                OperationStatus.FAILED, f"check-in page returned HTTP {page.status_code}"
            )
        if "今日已签到" in page.text:
            return OperationResult(
                OperationStatus.ALREADY_DONE, "daily check-in is already complete"
            )
        if dry_run:
            return OperationResult(
                OperationStatus.DRY_RUN, "check-in is available; submission skipped"
            )

        sitekey_match = re.search(r'data-sitekey=["\']([^"\']+)', page.text)
        sitekey = sitekey_match.group(1) if sitekey_match else KNOWN_TURNSTILE_SITEKEY
        try:
            captcha = self.captcha_solver.solve_turnstile(
                sitekey=sitekey,
                url=CHECKIN_PAGE,
                user_agent=self.client.settings.user_agent,
            )
        except CaptchaError as exc:
            return OperationResult(OperationStatus.CHALLENGE_BLOCKED, str(exc))
        body = {
            "qdxq": random.choice(("kx", "ng", "ym", "wl", "nu", "ch", "fd", "yl", "shuai")),
            "todaysay": "你好啊",
            "captcha_response": captcha.code,
            "hashkey": "",
            "version": 2,
        }
        try:
            response = self.client._request(
                "POST",
                CHECKIN_API,
                json=body,
                headers={"Referer": CHECKIN_PAGE},
            )
        except SiteRequestError as exc:
            return OperationResult(OperationStatus.FAILED, str(exc))
        payload = _json_payload(response)
        message = str(payload.get("msg", "")) if payload else response.text[:200]
        if response.status_code in {401, 403}:
            return OperationResult(
                OperationStatus.AUTH_REQUIRED, "check-in API rejected the session"
            )
        if CAPTCHA_ERROR_PATTERN.search(message):
            self._safe_report(captcha.task_id, correct=False)
            return OperationResult(
                OperationStatus.CHALLENGE_BLOCKED, "check-in challenge was rejected"
            )
        if payload and (payload.get("errno") == 0 or "已经签到" in message):
            self._safe_report(captcha.task_id, correct=True)
            status = (
                OperationStatus.ALREADY_DONE if "已经签到" in message else OperationStatus.SUCCESS
            )
            return OperationResult(status, message or "daily check-in succeeded")
        return OperationResult(
            OperationStatus.FAILED,
            message or f"check-in API returned HTTP {response.status_code}",
        )

    def fetch_question(self) -> tuple[DailyQuestion | None, OperationResult | None]:
        try:
            response = self.client._request(
                "GET", DAILY_QUESTION_API, headers={"Referer": QUESTION_PAGE}
            )
        except SiteRequestError as exc:
            return None, OperationResult(OperationStatus.FAILED, str(exc))
        if is_cloudflare_challenge(response):
            return None, OperationResult(
                OperationStatus.CHALLENGE_BLOCKED,
                "question API returned a Cloudflare managed edge challenge",
            )
        if response.status_code in {401, 403}:
            return None, OperationResult(
                OperationStatus.AUTH_REQUIRED, "question API rejected the session"
            )
        payload = _json_payload(response)
        if response.status_code != 200 or payload is None:
            return None, OperationResult(
                OperationStatus.FAILED,
                f"question API returned HTTP {response.status_code} or invalid JSON",
            )
        message = str(payload.get("msg", ""))
        if "已经答" in message or "已答" in message:
            return None, OperationResult(OperationStatus.ALREADY_DONE, message)
        raw_question = payload.get("question")
        if payload.get("errno") != 0 or not isinstance(raw_question, dict):
            return None, OperationResult(
                OperationStatus.FAILED, message or "question API response schema changed"
            )
        try:
            question = DailyQuestion(
                question_id=int(raw_question["id"]),
                text=str(raw_question["qc"]).strip(),
                options={index: str(raw_question[f"a{index}"]) for index in range(1, 5)},
            )
        except (KeyError, TypeError, ValueError) as exc:
            return None, OperationResult(
                OperationStatus.FAILED,
                f"question API response schema changed: {type(exc).__name__}",
            )
        return question, None

    def run_question(self, *, dry_run: bool) -> OperationResult:
        daily_status, terminal = self.fetch_daily_status()
        if terminal:
            return terminal
        assert daily_status is not None
        if daily_status["question"]:
            return OperationResult(
                OperationStatus.ALREADY_DONE, "daily question is already complete"
            )
        question, terminal = self.fetch_question()
        if terminal:
            return terminal
        assert question is not None
        resolution = self.question_bank.resolve(question)
        if resolution.status is not QuestionResolutionStatus.MATCHED:
            report = self.report_store.record(question, resolution)
            return OperationResult(
                OperationStatus.REVIEW_REQUIRED,
                resolution.message,
                {"report": str(report), "fingerprint": report.stem},
            )
        if dry_run:
            return OperationResult(
                OperationStatus.DRY_RUN,
                "approved answer matched; submission skipped",
                {"answer_index": resolution.answer_index},
            )
        try:
            captcha = self.captcha_solver.solve_turnstile(
                sitekey=KNOWN_TURNSTILE_SITEKEY,
                url=QUESTION_PAGE,
                user_agent=self.client.settings.user_agent,
            )
        except CaptchaError as exc:
            return OperationResult(OperationStatus.CHALLENGE_BLOCKED, str(exc))
        body = {
            "qid": question.question_id,
            "answer": resolution.answer_index,
            "captcha_response": captcha.code,
            "hashkey": "",
            "version": 2,
        }
        try:
            response = self.client._request(
                "POST",
                DAILY_QUESTION_API,
                json=body,
                headers={"Referer": QUESTION_PAGE},
            )
        except SiteRequestError as exc:
            return OperationResult(OperationStatus.FAILED, str(exc))
        payload = _json_payload(response)
        message = str(payload.get("msg", "")) if payload else response.text[:200]
        if response.status_code in {401, 403}:
            return OperationResult(
                OperationStatus.AUTH_REQUIRED, "question API rejected the session"
            )
        if CAPTCHA_ERROR_PATTERN.search(message):
            self._safe_report(captcha.task_id, correct=False)
            return OperationResult(
                OperationStatus.CHALLENGE_BLOCKED, "question challenge was rejected"
            )
        if payload and (payload.get("errno") == 0 or "已经答" in message):
            self._safe_report(captcha.task_id, correct=True)
            status = (
                OperationStatus.ALREADY_DONE if "已经答" in message else OperationStatus.SUCCESS
            )
            return OperationResult(status, message or "daily answer succeeded")

        rejected = QuestionResolution(
            QuestionResolutionStatus.ANSWER_REJECTED,
            resolution.answer_index,
            resolution.expected_answers,
            "site rejected an approved answer; human review required",
        )
        report = self.report_store.record(question, rejected, response_message=message)
        return OperationResult(
            OperationStatus.REVIEW_REQUIRED,
            rejected.message,
            {"report": str(report), "fingerprint": report.stem},
        )

    def _safe_report(self, task_id: str, *, correct: bool) -> None:
        with suppress(CaptchaError):
            self.captcha_solver.report(task_id, correct=correct)
