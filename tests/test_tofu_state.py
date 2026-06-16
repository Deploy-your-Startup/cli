import sys
from pathlib import Path

import pytest

sys.path.append(str(Path(__file__).parents[1] / "src"))

from cli import tofu_state


def test_resolve_default_provider_is_hetzner():
    config = tofu_state.resolve_state_config("demo", "production", {})
    assert config.provider == "hetzner"
    assert config.endpoint == tofu_state.STATE_BACKENDS["hetzner"]["endpoint"]
    assert config.region == "eu-central"
    assert config.bucket == tofu_state.DEFAULT_STATE_BUCKET
    assert config.key == "demo/production/terraform.tfstate"


def test_resolve_per_env_provider_ovh():
    config = tofu_state.resolve_state_config(
        "demo", "production", {"tofu_state_provider": "ovh"}
    )
    assert config.provider == "ovh"
    assert "ovh" in config.endpoint
    assert config.key == "demo/production/terraform.tfstate"


def test_resolve_overrides_and_validation():
    config = tofu_state.resolve_state_config(
        "demo",
        "staging",
        {"tofu_state_bucket": "custom-bucket", "tofu_state_region": "fsn1"},
    )
    assert config.bucket == "custom-bucket"
    assert config.region == "fsn1"

    with pytest.raises(Exception):
        tofu_state.resolve_state_config("demo", "production", {"tofu_state_provider": "aws"})
    with pytest.raises(Exception):
        tofu_state.resolve_state_config("", "production", {})


def test_render_backend_hcl_contains_key_fields():
    config = tofu_state.resolve_state_config("demo", "production", {})
    hcl = tofu_state.render_backend_hcl(config)
    assert 'bucket = "startup-tfstate"' in hcl
    assert 'key    = "demo/production/terraform.tfstate"' in hcl
    assert "use_lockfile = true" in hcl
    assert "use_path_style              = true" in hcl
    assert 'https://fsn1.your-objectstorage.com' in hcl


def test_write_backend_config(tmp_path):
    config = tofu_state.resolve_state_config("demo", "production", {})
    path = tofu_state.write_backend_config(tmp_path, "production", config)
    assert path.name == "backend.production.hcl"
    assert "startup-tfstate" in path.read_text()


def test_ensure_bucket_creates_when_missing(monkeypatch):
    calls = {"created": False}

    class _ClientError(Exception):
        pass

    class _FakeS3:
        def head_bucket(self, Bucket):
            raise _ClientError()

        def create_bucket(self, Bucket):
            calls["created"] = Bucket

    fake_boto3 = type("M", (), {"client": staticmethod(lambda *a, **k: _FakeS3())})
    fake_exc = type("E", (), {"ClientError": _ClientError})
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setitem(sys.modules, "botocore", type("B", (), {}))
    monkeypatch.setitem(sys.modules, "botocore.exceptions", fake_exc)

    config = tofu_state.resolve_state_config("demo", "production", {})
    created = tofu_state.ensure_bucket(config, "ak", "sk")
    assert created is True
    assert calls["created"] == "startup-tfstate"


def test_ensure_bucket_noop_when_exists(monkeypatch):
    class _FakeS3:
        def head_bucket(self, Bucket):
            return {}

        def create_bucket(self, Bucket):  # pragma: no cover - must not be called
            raise AssertionError("should not create existing bucket")

    fake_boto3 = type("M", (), {"client": staticmethod(lambda *a, **k: _FakeS3())})
    fake_exc = type("E", (), {"ClientError": Exception})
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setitem(sys.modules, "botocore", type("B", (), {}))
    monkeypatch.setitem(sys.modules, "botocore.exceptions", fake_exc)

    config = tofu_state.resolve_state_config("demo", "production", {})
    assert tofu_state.ensure_bucket(config, "ak", "sk") is False
