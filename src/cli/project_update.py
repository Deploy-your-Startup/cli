"""``startup update``: move one project to a release of the shared deployment.

A release is the immutable state that a sync publishes in the owner copy. The
project adopts it by recording the release commit for its roles and its shared
workflow references; nothing is deployed until the owner pushes that change.
"""

from __future__ import annotations

import contextlib
import io
import re
import subprocess
from pathlib import Path

import click

from cli.ansible_commands import (
    _candidate_repo_urls,
    _infer_roles_owner,
    _resolve_working_dir,
    run_pin,
)
from cli.sync_commands import sync_deploy_repo


def _pin(working_dir: Path) -> tuple[str, str]:
    """The project's recorded release commit and role checksum, empty if unpinned."""

    def read(name: str) -> str:
        path = working_dir / name
        return path.read_text().strip() if path.exists() else ""

    return read("shared-roles.ref"), read("shared-roles.sha256")


def _release_commit(working_dir: Path, release: str) -> str:
    """Resolve a release in the owner copy; empty when it is not published."""
    for url in _candidate_repo_urls(working_dir):
        result = subprocess.run(
            [
                "git",
                "ls-remote",
                url,
                f"refs/tags/{release}^{{}}",
                f"refs/tags/{release}",
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            lines = result.stdout.split()
            return lines[0] if lines else ""
    return ""


def _latest_release(owner: str, source_version: str, *, dry_run: bool) -> str:
    """Publish the template version as a release without moving the owner's main."""
    published: list[str] = []
    click.echo(f"Checking {owner}/deploy-your-startup for the latest release ...")
    # Sync reports against the owner's main, which this command never moves.
    with contextlib.redirect_stdout(io.StringIO()):
        sync_deploy_repo(
            owner=owner,
            source_version=source_version,
            tag_only=True,
            dry_run=dry_run,
            on_release=published.append,
        )
    if not published:
        raise click.ClickException(
            "Your shared deployment repository does not exist yet. Run startup sync first."
        )
    return published[0]


def _report_check(working_dir: Path, release: str) -> None:
    current, _checksum = _pin(working_dir)
    if current and current == _release_commit(working_dir, release):
        click.echo(f"Up to date: this project runs release {release}.")
        return
    click.echo(f"Update available: release {release}.")
    click.echo(f"This project runs {current[:12] or 'an unpinned revision'}.")
    click.echo(f"Adopt it with: startup update --version {release}")


def _report_update(
    before: tuple[str, str], working_dir: Path, release: str, moved: int
) -> None:
    after = _pin(working_dir)
    if after == before and not moved:
        click.echo(f"Up to date: this project already runs release {release}.")
        return
    roles = "unchanged" if after[1] == before[1] else "changed"
    click.echo(
        f"\nThis project now targets release {release} "
        f"({before[0][:12] or 'unpinned'} → {after[0][:12]}); role contents {roles}.\n"
        "Nothing is deployed yet. Review the diff, commit and push to roll it out.\n"
        "To go back, run: startup update --version <previous release or commit>"
    )


def _requested_release(
    working_dir: Path, owner: str, version: str | None, *, check: bool
) -> str:
    """A published release or commit as named; otherwise publish that template version."""
    is_commit = bool(version and re.fullmatch(r"[0-9a-f]{40}", version))
    if version and (is_commit or _release_commit(working_dir, version)):
        return version
    return _latest_release(owner, version or "main", dry_run=check)


def run_update(working_directory: str, *, version: str | None, check: bool) -> None:
    working_dir = _resolve_working_dir(working_directory)
    owner = _infer_roles_owner(working_dir)
    if not owner:
        raise click.ClickException(
            "Could not determine the GitHub owner from this project's origin remote."
        )
    release = _requested_release(working_dir, owner, version, check=check)
    if check:
        _report_check(working_dir, release)
        return
    before = _pin(working_dir)
    moved = run_pin(working_directory=working_directory, version=release, owner=owner)
    _report_update(before, working_dir, release, moved)
