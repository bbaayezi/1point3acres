# 1Point3Acres daily client

A modernized, testable personal client for checking daily status, submitting a daily check-in,
and reviewing the daily question bank.

> **Important:** 1Point3Acres currently prohibits unauthorized automated access in its Terms of
> Service. Confirm that you have permission before enabling live submissions. The included GitHub
> Actions workflow is manual-only and defaults to dry-run mode.

## What changed

- Python 3.12+ package and CLI instead of directory-dependent scripts.
- Current `requests` and `2captcha-python` dependencies.
- Cookie-first authentication with password login and interactive cookie fallback.
- Automatic persistence of cookies received through `Set-Cookie`.
- Explicit detection of expired sessions and Cloudflare managed edge challenges.
- No arbitrary HTTP 200 response is treated as a successful login.
- Unknown, changed, ambiguous, or rejected questions require human review.
- Check-in and question failures produce nonzero exit codes.
- Offline tests, linting, type checking, CI, and a manual Actions workflow.
- No credential-bearing JSON file is tracked by Git.

## Install

```bash
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
```

Run the offline verification suite:

```bash
ruff format --check src/onepoint3acres tests
ruff check src/onepoint3acres tests
mypy src/onepoint3acres
pytest
```

Optional read-only checks against the current public endpoints are gated separately:

```bash
RUN_LIVE_CONTRACT=1 pytest tests/test_live_contract.py
```

## Configuration

Configuration comes from environment variables. Do not store these in the repository.

| Variable | Purpose |
|---|---|
| `ONEPOINT3ACRES_COOKIE` | Initial browser `Cookie` request header |
| `ONEPOINT3ACRES_USERNAME` | Optional password-login username |
| `ONEPOINT3ACRES_PASSWORD` | Optional password-login password |
| `TWO_CAPTCHA_API_KEY` | Required only for live challenge submissions |
| `ONEPOINT3ACRES_USER_AGENT` | Browser identity associated with the cookie; defaults to Chrome on macOS |
| `ONEPOINT3ACRES_COOKIE_FILE` | Persistent cookie-jar location |
| `ONEPOINT3ACRES_PENDING_DIRECTORY` | Sanitized question-review reports |
| `ONEPOINT3ACRES_QUESTION_BANK_FILE` | Optional external question bank |
| `ONEPOINT3ACRES_REQUEST_TIMEOUT` | Request timeout in seconds; default `20` |
| `ONEPOINT3ACRES_DRY_RUN` | Set to `true` to prevent submissions |

Use a shell secret manager or a local file outside the repository to set them. Avoid putting a
cookie or password directly into shell history.

## Resilient login and cookie refresh

Authentication follows this sequence:

1. Load and validate the persisted cookie jar.
2. Merge and validate `ONEPOINT3ACRES_COOKIE`, when supplied.
3. Attempt username/password login when both values are configured.
4. If login remains unavailable and the terminal is interactive, prompt for a fresh browser cookie.
5. Validate the new cookie before saving it.

Cookies returned by the website are automatically merged and persisted after successful login and
daily operations. The cookie store and its single backup use mode `0600`; their parent directory
uses mode `0700`. Writes are atomic.

Cloudflare clearance cookies can be bound to the browser identity that created them. If a cookie
validates in the browser but not in this client, set `ONEPOINT3ACRES_USER_AGENT` to that browser's
exact user-agent string. The same value is also passed to 2captcha so challenge tokens and requests
use a consistent browser identity.

```bash
onepoint3acres auth status
onepoint3acres auth login
onepoint3acres auth import-cookie
onepoint3acres auth clear
```

An expired cookie cannot be refreshed if the website also refuses password login or requires
WeChat. In that case, perform a browser login and run `auth import-cookie`.

## Daily operations

Start with a read-only run:

```bash
onepoint3acres run --dry-run
```

After authorization and controlled live verification:

```bash
onepoint3acres run
onepoint3acres run --checkin-only
onepoint3acres run --question-only
```

Exit codes:

| Code | Meaning |
|---:|---|
| `0` | Success, already complete, skipped, or dry-run |
| `1` | Network, response, or submission failure |
| `2` | Authentication or configuration requires attention |
| `3` | Daily question requires human review |
| `4` | Unsupported or rejected challenge |

The client deliberately does not pass a Cloudflare managed edge challenge to the standalone
Turnstile solver. Those challenges require different browser-bound parameters and should not be
silently treated as ordinary widgets.

## Daily-question review

Unknown questions and changed answer options are never submitted. The client writes a sanitized,
deduplicated report containing only the question, answer options, timestamps, and expected answer
text.

```bash
onepoint3acres questions pending
onepoint3acres questions approve path/to/report.json --answer 3
```

Approval displays the selected answer and asks for confirmation. It adds the answer text—not the
option number—to [`question_bank.json`](src/onepoint3acres/question_bank.json), because the option
order may change. Commit the bank update with an accompanying test when possible.

## GitHub Actions

[`ci.yml`](.github/workflows/ci.yml) runs fully offline tests against Python 3.12, 3.13, and 3.14.

[`daily.yml`](.github/workflows/daily.yml) must initially be triggered manually. Create a GitHub
environment named `daily` and add whichever secrets are appropriate:

- `ONEPOINT3ACRES_COOKIE`
- `ONEPOINT3ACRES_USERNAME` and `ONEPOINT3ACRES_PASSWORD`
- `TWO_CAPTCHA_API_KEY` for a live submission

If the cookie was captured from a browser whose identity differs from the default, also create the
repository variable `ONEPOINT3ACRES_USER_AGENT` with that browser's exact user-agent string.

Run it with `dry_run=true` first. GitHub-hosted runners may receive Cloudflare challenges because
they use datacenter IP addresses. A trusted self-hosted runner is more predictable, but should only
be used after confirming authorization.

GitHub-hosted runners are ephemeral, so refreshed cookies do not survive the job. On a self-hosted
runner, set the repository variable `ONEPOINT3ACRES_COOKIE_FILE` to a protected persistent path
owned by the runner account. Do not put cookies in Actions cache or upload them as artifacts.

Only sanitized pending-question reports are uploaded, with seven-day retention. Passwords, cookies,
CSRF values, and CAPTCHA tokens are never included.

After multiple successful manual dry-runs and controlled live runs, a `schedule` trigger can be
added. Keep `workflow_dispatch` for recovery and testing, and schedule away from the start of an
hour to reduce GitHub Actions delays.

## Local Codex daily job

For a local recurring Codex job, keep the validated cookie jar and 2captcha key outside the
repository under `~/.local/state/onepoint3acres/`, with directory mode `0700` and file mode `0600`.
Install the project into `.venv`, then use the secret-safe launcher:

```bash
scripts/run_daily.sh --dry-run
scripts/run_daily.sh
```

The launcher never prints secret values. It reuses the persistent cookie jar, writes sanitized
question-review reports beside it, and exits before requesting a CAPTCHA when the authenticated
status endpoint reports that the day's operations are already complete.
