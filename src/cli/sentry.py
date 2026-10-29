"""Project-specific Sentry projects; the DSN only travels through captured stdin.

The login belongs to `sentry-cli login`, which keeps the token in
~/.sentryclirc. Like the Auth0 CLI's login it is reused, never printed or
passed as an argument; in a terminal without one, `sentry-cli login` runs
first. SENTRY_AUTH_TOKEN, SENTRY_URL and SENTRY_ORG override the file, as they
do for sentry-cli.
"""

from __future__ import annotations

import configparser
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import click
import httpx

from cli.vault_fields import store_vault_fields

DEFAULT_URL = "https://sentry.io"
DSN_FIELD = "backend_sentry_dsn"
PLATFORM = "python-django"
LOGIN_HINT = (
    "Run `sentry-cli login` (install: `brew install getsentry/tools/sentry-cli`); "
    "for an EU organization run `sentry-cli --url https://de.sentry.io login`."
)


def _sentryclirc() -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    config.read(Path.home() / ".sentryclirc")
    return config


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stderr.isatty()


def _token(config: configparser.ConfigParser) -> str:
    return os.environ.get("SENTRY_AUTH_TOKEN") or config.get(
        "auth", "token", fallback=""
    )


def ensure_sentry_login() -> None:
    """Run `sentry-cli login` in a terminal that has no login yet."""
    if _token(_sentryclirc()) or not _interactive():
        return
    if not shutil.which("sentry-cli"):
        raise click.ClickException(
            "sentry-cli is missing: run `brew install getsentry/tools/sentry-cli` "
            "or `npm install -g @sentry/cli`, then try again."
        )
    click.echo(
        "No Sentry login found; starting `sentry-cli login`. Its token needs "
        "org:read, team:read, project:read and project:write."
    )
    command = ["sentry-cli"]
    if os.environ.get("SENTRY_URL"):
        command += ["--url", os.environ["SENTRY_URL"]]
    if subprocess.run([*command, "login"], check=False).returncode:
        raise click.ClickException(f"Sentry login did not complete. {LOGIN_HINT}")


def default_org() -> str | None:
    return os.environ.get("SENTRY_ORG") or _sentryclirc().get(
        "defaults", "org", fallback=None
    )


@dataclass
class SentryApi:
    url: str
    token: str

    @classmethod
    def from_login(cls) -> SentryApi:
        ensure_sentry_login()
        config = _sentryclirc()
        token = _token(config)
        if not token:
            raise click.ClickException(f"No Sentry login found. {LOGIN_HINT}")
        url = os.environ.get("SENTRY_URL") or config.get(
            "defaults", "url", fallback=DEFAULT_URL
        )
        return cls(url.rstrip("/"), token)

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
                "Sentry rejected the login or a required scope (org:read, "
                f"team:read, project:read, project:write). {LOGIN_HINT}"
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
