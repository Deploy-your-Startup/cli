"""Project-specific Sentry projects; the DSN only travels through captured stdin.

The login is a personal token in ~/.sentryclirc (see `cli.sentry_login`); it is
reused, never printed or passed as an argument.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import click
import httpx

from cli.sentry_login import (
    LOGIN_HINT,
    ensure_sentry_login,
    saved_token,
    sentry_url,
    sentryclirc,
)
from cli.vault_fields import store_vault_fields

DSN_FIELD = "backend_sentry_dsn"
PLATFORM = "python-django"


def default_org() -> str | None:
    return os.environ.get("SENTRY_ORG") or sentryclirc().get(
        "defaults", "org", fallback=None
    )


@dataclass
class SentryApi:
    url: str
    token: str

    @classmethod
    def from_login(cls) -> SentryApi:
        ensure_sentry_login()
        config = sentryclirc()
        token = saved_token(config)
        if not token:
            raise click.ClickException(f"No Sentry login found. {LOGIN_HINT}")
        return cls(sentry_url(config), token)

    def request(self, method: str, path: str, payload: dict | None = None):
        """Return the JSON body, or None for 404; never echo provider output."""
        try:
            response = httpx.request(
                method,
                f"{self.url}/api/0/{path}",
                headers={"Authorization": f"Bearer {self.token}"},
                json=payload,
                timeout=30,
            )
        except httpx.HTTPError:
            raise click.ClickException(
                f"Sentry is unreachable at {self.url}. Check the network and URL."
            ) from None
        if response.status_code == 404:
            return None
        if response.status_code in (401, 403):
            raise click.ClickException(
                f"Sentry rejected the login or a required scope. {LOGIN_HINT}"
            )
        if response.is_error:
            raise click.ClickException(
                f"Sentry request failed with HTTP {response.status_code}. "
                "The response has been withheld."
            )
        return response.json()


def check_sentry_login(org: str) -> SentryApi:
    """Validate organization, team and project access before creating anything."""
    api = SentryApi.from_login()
    organization = api.request("GET", f"organizations/{org}/")
    if organization is None:
        raise click.ClickException(
            f"Sentry organization '{org}' was not found for this login."
        )
    # EU organizations answer on their own region host.
    region = organization.get("links", {}).get("regionUrl")
    if region:
        api.url = region.rstrip("/")
    api.request("GET", f"organizations/{org}/teams/")
    api.request("GET", f"organizations/{org}/projects/")
    return api


def dsn_file(project_dir: Path) -> Path:
    for path in sorted((project_dir / "deployment" / "group_vars").glob("*.yml")):
        if f"{DSN_FIELD}:" in path.read_text():
            return path.resolve()
    raise click.ClickException(f"This template has no {DSN_FIELD} setting.")


def _team(api: SentryApi, org: str, team: str | None) -> str:
    slugs = [
        entry["slug"] for entry in api.request("GET", f"organizations/{org}/teams/")
    ]
    if team:
        if team not in slugs:
            raise click.ClickException(f"Sentry team '{team}' does not exist in {org}.")
        return team
    if len(slugs) == 1:
        return slugs[0]
    if org in slugs:
        return org
    raise click.ClickException(
        "Choose a Sentry team with --team: " + ", ".join(slugs or ["(none)"])
    )


def _ensure_project(api: SentryApi, org: str, name: str, team: str | None) -> None:
    if api.request("GET", f"projects/{org}/{name}/") is not None:
        return
    api.request(
        "POST",
        f"teams/{org}/{_team(api, org, team)}/projects/",
        {"name": name, "slug": name, "platform": PLATFORM},
    )


def _public_dsn(api: SentryApi, org: str, name: str) -> str:
    keys = api.request("GET", f"projects/{org}/{name}/keys/") or []
    for key in keys:
        if key.get("isActive", True) and key.get("dsn", {}).get("public"):
            return key["dsn"]["public"]
    raise click.ClickException(f"Sentry project {name} has no active client key.")


def configure_sentry(
    project_dir: Path, project_name: str, org: str, team: str | None = None
) -> None:
    target = dsn_file(project_dir)
    api = check_sentry_login(org)
    _ensure_project(api, org, project_name, team)
    store_vault_fields(
        project_dir, target, {DSN_FIELD: _public_dsn(api, org, project_name)}, "Sentry"
    )
    click.echo(
        f"Sentry project {project_name} configured in {org}; DSN saved in the project vault."
    )
