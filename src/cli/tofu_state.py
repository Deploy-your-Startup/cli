"""Per-environment OpenTofu remote state configuration.

State lives on S3-compatible object storage at the same provider as the
environment's infrastructure — selected per environment via `tofu_state_provider`
in group_vars (Hetzner now, OVH later). The CLI renders backend.<env>.hcl so no
one writes backend config by hand, and the state of each environment is kept with
its own provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import click

# Endpoint + region per state provider. Both are S3-compatible object storage;
# per-env overrides are possible via group_vars (tofu_state_endpoint/region).
STATE_BACKENDS: dict[str, dict[str, str]] = {
    "hetzner": {
        "endpoint": "https://fsn1.your-objectstorage.com",
        "region": "eu-central",
    },
    "ovh": {
        "endpoint": "https://s3.gra.io.cloud.ovh.net",
        "region": "gra",
    },
}

DEFAULT_STATE_BUCKET = "startup-tfstate"
DEFAULT_STATE_PROVIDER = "hetzner"


@dataclass
class StateConfig:
    provider: str
    endpoint: str
    region: str
    bucket: str
    key: str


def resolve_state_config(
    project_name: str, environment: str, group_vars: dict
) -> StateConfig:
    """Build the state backend config for an environment from its group_vars."""
    provider = str(
        group_vars.get("tofu_state_provider", DEFAULT_STATE_PROVIDER)
    ).lower()
    backend = STATE_BACKENDS.get(provider)
    if backend is None:
        raise click.ClickException(
            f"Unknown tofu_state_provider '{provider}'. "
            f"Supported: {', '.join(sorted(STATE_BACKENDS))}."
        )
    if not project_name:
        raise click.ClickException("project_name is required to derive the state key.")
    return StateConfig(
        provider=provider,
        endpoint=str(group_vars.get("tofu_state_endpoint", backend["endpoint"])),
        region=str(group_vars.get("tofu_state_region", backend["region"])),
        bucket=str(group_vars.get("tofu_state_bucket", DEFAULT_STATE_BUCKET)),
        key=f"{project_name}/{environment}/terraform.tfstate",
    )


def render_backend_hcl(config: StateConfig) -> str:
    """Render a partial-backend config file for `tofu init -backend-config=`."""
    return (
        "\n".join(
            [
                f'bucket = "{config.bucket}"',
                f'key    = "{config.key}"',
                f'region = "{config.region}"',
                "endpoints = {",
                f'  s3 = "{config.endpoint}"',
                "}",
                "encrypt      = true",
                # Native S3 locking (OpenTofu >= 1.10) — no DynamoDB.
                "use_lockfile = true",
                # Non-AWS S3: skip AWS-specific validations.
                "skip_credentials_validation = true",
                "skip_metadata_api_check     = true",
                "skip_region_validation      = true",
                "skip_requesting_account_id  = true",
                "use_path_style              = true",
            ]
        )
        + "\n"
    )


def write_backend_config(tofu_path: Path, environment: str, config: StateConfig) -> Path:
    path = tofu_path / f"backend.{environment}.hcl"
    path.write_text(render_backend_hcl(config))
    return path


def console_hint(provider: str) -> str:
    """Where to generate S3 credentials for a provider (Hetzner has no API)."""
    if provider == "hetzner":
        return (
            "Hetzner Cloud Console -> your project -> Object Storage -> "
            "create credentials (the secret is shown only once)."
        )
    if provider == "ovh":
        return "OVH: S3 credentials can be minted via the API (no console step)."
    return f"the {provider} console."


def ensure_bucket(config: StateConfig, access_key: str, secret_key: str) -> bool:
    """Create the state bucket if it does not exist. Returns True if it was created.

    Uses boto3 (an optional dependency only needed for this one-time, operator-run
    setup — never at deploy time). Raises a helpful error if boto3 is missing.
    """
    try:
        import boto3
        from botocore.exceptions import ClientError
    except ImportError as exc:  # pragma: no cover - exercised only without boto3
        raise click.ClickException(
            "boto3 is required to create the state bucket. Install it with "
            "`pip install boto3` (or `uv pip install boto3`), or create the bucket "
            f"'{config.bucket}' manually: {console_hint(config.provider)}"
        ) from exc

    s3 = boto3.client(
        "s3",
        endpoint_url=config.endpoint,
        region_name=config.region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )
    try:
        s3.head_bucket(Bucket=config.bucket)
        return False
    except ClientError:
        pass
    s3.create_bucket(Bucket=config.bucket)
    return True
