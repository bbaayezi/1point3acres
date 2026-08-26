from __future__ import annotations

from typing import Protocol

from .models import CaptchaSolution


class CaptchaError(RuntimeError):
    pass


class CaptchaSolver(Protocol):
    def solve_turnstile(self, *, sitekey: str, url: str, user_agent: str) -> CaptchaSolution: ...

    def report(self, task_id: str, *, correct: bool) -> None: ...


class UnavailableCaptchaSolver:
    def solve_turnstile(self, *, sitekey: str, url: str, user_agent: str) -> CaptchaSolution:
        del sitekey, url, user_agent
        raise CaptchaError("TWO_CAPTCHA_API_KEY is required for CAPTCHA solving")

    def report(self, task_id: str, *, correct: bool) -> None:
        del task_id, correct


class TwoCaptchaSolver:
    def __init__(self, api_key: str) -> None:
        from twocaptcha import TwoCaptcha

        self._solver = TwoCaptcha(api_key)

    def solve_turnstile(self, *, sitekey: str, url: str, user_agent: str) -> CaptchaSolution:
        try:
            result = self._solver.turnstile(sitekey=sitekey, url=url, useragent=user_agent)
            return CaptchaSolution(task_id=str(result["captchaId"]), code=str(result["code"]))
        except Exception as exc:
            raise CaptchaError(f"2Captcha Turnstile task failed: {type(exc).__name__}") from exc

    def report(self, task_id: str, *, correct: bool) -> None:
        try:
            self._solver.report(task_id, correct)
        except Exception as exc:
            raise CaptchaError(f"2Captcha report failed: {type(exc).__name__}") from exc
