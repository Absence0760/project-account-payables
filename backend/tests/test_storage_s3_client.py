"""S3 client factory config fallback (deployed vs local-first).

The shared factory (`storage._get_client`) must pass the MinIO endpoint +
static keys when configured (the committed dev defaults), and omit them when
empty so boto3 falls back to real AWS S3 + the default credential chain
(instance profile / env) — the minimal-deployment story relies on this.
"""

from unittest.mock import patch

from app.services import storage


def _client_kwargs(monkeypatch, *, endpoint, access_key, secret_key):
    monkeypatch.setattr(storage.settings, "s3_endpoint_url", endpoint)
    monkeypatch.setattr(storage.settings, "s3_access_key", access_key)
    monkeypatch.setattr(storage.settings, "s3_secret_key", secret_key)
    with patch.object(storage, "boto3") as boto3_mock:
        storage._get_client()
    args, kwargs = boto3_mock.client.call_args
    assert args == ("s3",)
    return kwargs


def test_dev_defaults_pass_endpoint_and_static_keys(monkeypatch):
    kwargs = _client_kwargs(
        monkeypatch,
        endpoint="http://localhost:9000",
        access_key="minioadmin",
        secret_key="minioadmin",
    )
    assert kwargs == {
        "endpoint_url": "http://localhost:9000",
        "aws_access_key_id": "minioadmin",
        "aws_secret_access_key": "minioadmin",
    }


def test_empty_config_uses_real_s3_and_default_credential_chain(monkeypatch):
    kwargs = _client_kwargs(monkeypatch, endpoint="", access_key="", secret_key="")
    assert kwargs == {}


def test_endpoint_without_keys_still_targets_endpoint(monkeypatch):
    kwargs = _client_kwargs(
        monkeypatch, endpoint="http://localhost:4566", access_key="", secret_key=""
    )
    assert kwargs == {"endpoint_url": "http://localhost:4566"}


def test_half_configured_key_pair_falls_back_to_credential_chain(monkeypatch):
    kwargs = _client_kwargs(monkeypatch, endpoint="", access_key="only-one", secret_key="")
    assert kwargs == {}
