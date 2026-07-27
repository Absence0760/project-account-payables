"""hCaptcha server-side verification.

The frontend renders an hCaptcha widget and posts the resulting response
token along with the signup form. The backend posts that token to hCaptcha's
siteverify endpoint with the secret key to confirm the user is human.

If FEOH_HCAPTCHA_SECRET is empty (local dev), verification is skipped and any
token value is accepted — this keeps the flow testable without hitting the
hCaptcha API from laptops.
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

SITEVERIFY_URL = "https://hcaptcha.com/siteverify"


class CaptchaError(ValueError):
    """Raised when the submitted captcha token is invalid or missing."""


async def verify_captcha(token: str | None, remote_ip: str | None = None) -> None:
    """Validate a captcha response token with hCaptcha. No-op when unconfigured."""
    if not settings.hcaptcha_secret:
        # WARNING (not DEBUG) so an accidentally-unset secret in a deployed env
        # is visible in log sinks rather than silently disabling the gate. A
        # hard startup guard (config.py) blocks this for non-dev environments.
        logger.warning("hCaptcha secret not configured — captcha verification is DISABLED.")
        return

    if not token:
        raise CaptchaError("Captcha is required.")

    data = {"secret": settings.hcaptcha_secret, "response": token}
    if remote_ip:
        data["remoteip"] = remote_ip

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(SITEVERIFY_URL, data=data)
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPError as exc:
        # Log the class only — the exception text can embed the request URL /
        # response body (which carries the client-supplied token).
        logger.warning("hCaptcha network error: %s", type(exc).__name__)
        raise CaptchaError("Captcha verification is temporarily unavailable.") from exc

    if not payload.get("success"):
        error_codes = payload.get("error-codes", [])
        logger.info("hCaptcha rejected token: %s", error_codes)
        raise CaptchaError("Captcha verification failed. Please try again.")
