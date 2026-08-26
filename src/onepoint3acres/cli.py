from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path
from typing import NoReturn

from .captcha import TwoCaptchaSolver, UnavailableCaptchaSolver
from .client import SiteClient, SiteRequestError
from .config import Settings
from .cookies import CookieStore, CookieStoreError
from .daily import DailyRunner
from .models import AuthStatus, DailyRunResult
from .question_bank import QuestionBank, QuestionBankError, bundled_bank_path
from .reports import QuestionReportStore


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="onepoint3acres")
    subcommands = parser.add_subparsers(dest="command", required=True)

    run = subcommands.add_parser("run", help="run daily check-in and question workflow")
    run.add_argument("--dry-run", action="store_true", help="read and resolve without submitting")
    run.add_argument("--non-interactive", action="store_true", help="never prompt for a cookie")
    selection = run.add_mutually_exclusive_group()
    selection.add_argument("--checkin-only", action="store_true")
    selection.add_argument("--question-only", action="store_true")

    auth = subcommands.add_parser("auth", help="manage authentication")
    auth_commands = auth.add_subparsers(dest="auth_command", required=True)
    auth_commands.add_parser("status", help="validate the current session")
    auth_commands.add_parser("login", help="try cookie, password, then interactive cookie login")
    auth_commands.add_parser("import-cookie", help="import and validate a browser cookie")
    auth_commands.add_parser("clear", help="remove the persisted cookie jar")

    questions = subcommands.add_parser("questions", help="manage pending questions")
    question_commands = questions.add_subparsers(dest="question_command", required=True)
    pending = question_commands.add_parser("pending", help="list pending question reports")
    pending.add_argument("--directory", type=Path)
    approve = question_commands.add_parser("approve", help="approve an answer from a report")
    approve.add_argument("report", type=Path)
    approve.add_argument("--answer", type=int, choices=(1, 2, 3, 4), required=True)
    approve.add_argument("--bank", type=Path, default=bundled_bank_path())
    approve.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    return parser


def _cookie_prompt(reason: str) -> str | None:
    print(f"Password login was unavailable: {reason}", file=sys.stderr)
    print("Log in with a browser, then paste a fresh Cookie request header.", file=sys.stderr)
    value = getpass.getpass("Cookie (hidden): ").strip()
    return value or None


def _solver(settings: Settings) -> TwoCaptchaSolver | UnavailableCaptchaSolver:
    if settings.two_captcha_key:
        return TwoCaptchaSolver(settings.two_captcha_key)
    return UnavailableCaptchaSolver()


def _client(settings: Settings) -> SiteClient:
    return SiteClient(settings, _solver(settings))


def _print_result(result: DailyRunResult) -> None:
    payload = {
        "account": result.account,
        "auth": {"status": result.auth.status.value, "message": result.auth.message},
        "checkin": {
            "status": result.checkin.status.value,
            "message": result.checkin.message,
            "details": result.checkin.details,
        },
        "question": {
            "status": result.question.status.value,
            "message": result.question.message,
            "details": result.question.details,
        },
        "exit_code": result.exit_code,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        lines = [
            "## 1Point3Acres daily result",
            "",
            "| Step | Status | Message |",
            "|---|---|---|",
            f"| Authentication | {result.auth.status.value} | {result.auth.message} |",
            f"| Check-in | {result.checkin.status.value} | {result.checkin.message} |",
            f"| Question | {result.question.status.value} | {result.question.message} |",
            "",
        ]
        with Path(summary_path).open("a", encoding="utf-8") as stream:
            stream.write("\n".join(lines))


def _run(settings: Settings, arguments: argparse.Namespace) -> int:
    errors = settings.validate(require_captcha=not (settings.dry_run or arguments.dry_run))
    if errors:
        print("Configuration error: " + "; ".join(errors), file=sys.stderr)
        return 2
    client = _client(settings)
    interactive = not arguments.non_interactive and sys.stdin.isatty() and not os.getenv("CI")
    auth = client.authenticate(cookie_prompt=_cookie_prompt if interactive else None)
    bank = QuestionBank.load(settings.question_bank_file)
    reports = QuestionReportStore(settings.pending_directory)
    runner = DailyRunner(client, client.captcha_solver, bank, reports)
    result = runner.run(
        auth,
        dry_run=settings.dry_run or arguments.dry_run,
        checkin_only=arguments.checkin_only,
        question_only=arguments.question_only,
    )
    _print_result(result)
    return result.exit_code


def _auth(settings: Settings, arguments: argparse.Namespace) -> int:
    store = CookieStore(settings.cookie_file)
    if arguments.auth_command == "clear":
        print("Persisted cookie removed." if store.clear() else "No persisted cookie was present.")
        return 0
    client = _client(settings)
    if arguments.auth_command == "status":
        result = client.validate_session()
    elif arguments.auth_command == "import-cookie":
        value = getpass.getpass("Cookie (hidden): ").strip()
        if not value:
            print("No cookie supplied.", file=sys.stderr)
            return 2
        try:
            client.replace_with_cookie_header(value)
        except CookieStoreError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        result = client.validate_session()
        if result.ok:
            client.persist_cookies()
    else:
        result = client.authenticate(cookie_prompt=_cookie_prompt)
    print(json.dumps({"status": result.status.value, "message": result.message}, indent=2))
    return 0 if result.status is AuthStatus.SUCCESS else 2


def _questions(settings: Settings, arguments: argparse.Namespace) -> int:
    if arguments.question_command == "pending":
        store = QuestionReportStore(arguments.directory or settings.pending_directory)
        reports = store.list_reports()
        for report in reports:
            print(report)
        return 0 if reports else 1

    report_path: Path = arguments.report
    bank_path: Path = arguments.bank
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
        answer = report["options"][str(arguments.answer)]
        print(f"Question: {report['question']}")
        print(f"Approved answer: {answer}")
        if not arguments.yes:
            confirmation = input("Add this answer to the question bank? [y/N] ").strip().lower()
            if confirmation not in {"y", "yes"}:
                print("No changes made.")
                return 1
        source = bank_path if bank_path.exists() else None
        bank = QuestionBank.load(source)
        bank.approve_report(report_path, answer_index=arguments.answer, output=bank_path)
    except (OSError, json.JSONDecodeError, KeyError, QuestionBankError) as exc:
        print(f"Cannot approve question: {exc}", file=sys.stderr)
        return 2
    print(f"Question bank updated: {bank_path}")
    return 0


def main(argv: list[str] | None = None) -> NoReturn:
    arguments = _parser().parse_args(argv)
    settings = Settings.from_env()
    try:
        if arguments.command == "run":
            code = _run(settings, arguments)
        elif arguments.command == "auth":
            code = _auth(settings, arguments)
        else:
            code = _questions(settings, arguments)
    except (SiteRequestError, CookieStoreError, QuestionBankError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
