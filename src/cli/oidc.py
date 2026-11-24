"""Connect a project to a preconfigured OpenID Connect client."""

import base64
import secrets
from pathlib import Path
from urllib.parse import urlsplit

import click
import httpx

from cli.vault_fields import read_vault_field, store_vault_fields


def _https_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        return (
            url.scheme == "https"
            and bool(url.hostname)
            and url.username is None
            and "#" not in value
            and not any(char.isspace() for char in value)
        )
    except ValueError:
        return False


def discover(issuer: str) -> dict:
    """Load metadata and match the exact issuer, including its trailing slash."""
    if not _https_url(issuer) or "?" in issuer:
        raise click.ClickException("Enter a complete HTTPS OIDC issuer URL.")
    try:
        response = httpx.get(
            issuer.rstrip("/") + "/.well-known/openid-configuration", timeout=10
        )
        response.raise_for_status()
        metadata = response.json()
    except (httpx.HTTPError, ValueError):
        raise click.ClickException("Could not read OIDC discovery metadata.") from None
    if not isinstance(metadata, dict) or metadata.get("issuer") != issuer:
        raise click.ClickException(
            "OIDC discovery issuer does not exactly match the configured URL. "
            "Copy the issuer from discovery, including any trailing slash."
        )
    for name in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
        endpoint = metadata.get(name)
        if not isinstance(endpoint, str) or not _https_url(endpoint):
            raise click.ClickException(f"OIDC discovery must publish an HTTPS {name}.")
    return metadata


def configure(
    project_dir: Path, issuer: str, client_id: str, client_secret: str
) -> None:
    target = (project_dir / "deployment/group_vars/production.yml").resolve()
    cookie = read_vault_field(target, "oauth2_proxy_cookie_secret")
    store_vault_fields(
        project_dir,
        target,
        {
            "oauth2_proxy_client_id": client_id,
            "oauth2_proxy_client_secret": client_secret,
            "oauth2_proxy_cookie_secret": cookie
            or base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
            "oauth2_proxy_oidc_issuer_url": issuer,
            "oauth2_proxy_bearer_enabled": "false",
            "oauth2_proxy_enabled": "true",
        },
        "OIDC",
    )
