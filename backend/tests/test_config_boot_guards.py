"""Boot-time config guards (config.py model validators).

A deployed environment must refuse to start with a fail-open captcha or the
well-known default / weak JWT signing key — both are silent
misconfigurations that would otherwise ship to production. Local-dev / CI
envs keep the convenient defaults.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings

# A throwaway 32+ char key for the "good config" cases.
_GOOD_KEY = "x" * 48


def test_deployed_env_refuses_default_secret_key():
    with pytest.raises(ValidationError, match="AP_SECRET_KEY"):
        Settings(
            environment="production",
            hcaptcha_secret="hc",
            secret_key="change-me-in-production",
        )


def test_deployed_env_refuses_too_short_secret_key():
    with pytest.raises(ValidationError, match="AP_SECRET_KEY"):
        Settings(environment="production", hcaptcha_secret="hc", secret_key="short")


def test_deployed_env_accepts_a_strong_secret_key():
    s = Settings(environment="production", hcaptcha_secret="hc", secret_key=_GOOD_KEY)
    assert s.secret_key == _GOOD_KEY


def test_local_dev_keeps_the_default_secret_key():
    # The default convenience key is fine in a non-deployed env — a fresh clone
    # must run with no secret setup.
    s = Settings(environment="development", secret_key="change-me-in-production")
    assert s.secret_key == "change-me-in-production"
