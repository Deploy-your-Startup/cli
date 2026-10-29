"""`startup sentry`: optional error tracking with the sentry-cli login."""

from __future__ import annotations

import sys
from pathlib import Path

import click

from cli.sentry import (
    check_sentry_login,
    configure_sentry,
    default_org,
    ensure_sentry_login,
)


def _org(org: str | None) -> str:
    # The login comes first: it may leave the default organization behind.
    ensure_sentry_login()
    resolved = org or default_org()
    if not resolved and sys.stdin.isatty():
        resolved = click.prompt("Sentry organization slug")
    if not resolved:
        raise click.BadParameter(
            "Pass the organization slug or set [defaults] org in ~/.sentryclirc.",
            param_hint="--org",
        )
    return resolved


@click.group(name="sentry")
def sentry():
    """Configure optional error tracking with the sentry-cli login."""


@sentry.command("check")
@click.option("--org", help="Sentry organization slug (default: ~/.sentryclirc).")
def check(org):
    """Validate Sentry access before creating a project."""
    org = _org(org)
    check_sentry_login(org)
    click.echo(f"Sentry access to {org} verified.")


@sentry.command("setup")
@click.option("--org", help="Sentry organization slug (default: ~/.sentryclirc).")
@click.option("--team", help="Team for a new project (default: the only team).")
@click.option("--project-name", required=True)
@click.option(
    "--project-dir", type=click.Path(path_type=Path, exists=True), default="."
)
def setup(org, team, project_name, project_dir):
    """Create or reuse the project's Sentry project and vault its DSN."""
    configure_sentry(project_dir.resolve(), project_name, _org(org), team)
