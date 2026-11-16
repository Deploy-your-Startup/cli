"""``startup migrate deploy``: move one project to another shared repository.

Typically from a per-owner ``<owner>/deploy-your-startup`` copy to the public
``Deploy-your-Startup/deploy``, or to a fork of it. The project is pinned to the
current release of the new source; the old pin is not reproduced.
"""

from __future__ import annotations

from pathlib import Path

import click

from cli.ansible_commands import FOLLOW_MARKER, _resolve_working_dir, run_pin
from cli.project_update import _remote_tags
from cli.shared_source import (
    DEFAULT_SHARED_REPOSITORY,
    REPOSITORY,
    SOURCE_FILE,
    latest_release,
    legacy_copy,
    shared_repository,
)
from cli.workflow_pins import move_workflow_references

PIN_FILES = ("shared-roles.ref", "shared-roles.sha256")


def _record_source(working_dir: Path, target: str) -> None:
    path = working_dir / SOURCE_FILE
    if target == DEFAULT_SHARED_REPOSITORY:
        path.unlink(missing_ok=True)
    else:
        path.write_text(target + "\n")


def _target_release(working_dir: Path, target: str) -> tuple[str, str]:
    tags = _remote_tags(working_dir, target)
    release = latest_release(list(tags))
    if not release:
        raise click.ClickException(f"{target} has no release yet; nothing to pin.")
    return release, tags[release]


def run_migrate(working_directory: str, *, source: str | None, dry_run: bool) -> None:
    working_dir = _resolve_working_dir(working_directory)
    target = source or DEFAULT_SHARED_REPOSITORY
    if not REPOSITORY.fullmatch(target):
        raise click.ClickException("--source must name one owner/repository.")
    legacy = legacy_copy(working_dir)
    previous = {name for name in (legacy, shared_repository(working_dir)) if name}
    previous.discard(target)
    if not previous:
        click.echo(f"Already uses {target}; nothing to migrate.")
        return
    release, commit = _target_release(working_dir, target)
    old = ", ".join(sorted(previous))
    if dry_run:
        click.echo(
            f"Would move this project from {old} to {target} and pin release "
            f"{release} ({commit[:12]}). Nothing was changed."
        )
        return

    moved = move_workflow_references(working_dir.parent, previous, target)
    _record_source(working_dir, target)
    for name in (*PIN_FILES, FOLLOW_MARKER):
        (working_dir / name).unlink(missing_ok=True)
    run_pin(working_directory=working_directory, version=release, repository=target)
    click.echo(
        f"\nThis project now uses {target} release {release}: {moved} workflow "
        f"reference(s) moved from {old}.\n"
        "Nothing is deployed yet. Review the diff, commit and push to roll it out."
    )
    if legacy:
        click.echo(
            f"Once no project calls {legacy} anymore, archive it instead of "
            "deleting it, so old pins stay available:\n"
            f"  gh repo archive {legacy}"
        )
