"""``startup update``: move one project to a release of the shared deployment.

A release is an immutable tag of the project's shared repository
(``shared_source.shared_repository``), published there with
``mise run release vX.Y.Z``. The project adopts it by recording the release
commit for its roles and its shared workflow references; nothing is deployed
until the owner pushes that change.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import click

from cli.ansible_commands import _candidate_repo_urls, _resolve_working_dir, run_pin
from cli.project_follow import (
    follow_latest,
    follows_latest,
    report_following,
    stop_following,
)
from cli.shared_source import (
    DEFAULT_SHARED_REPOSITORY,
    latest_release,
    legacy_copy,
    shared_repository,
)

LATEST = "latest"
COMMIT = re.compile(r"[0-9a-f]{40}")


def _pin(working_dir: Path) -> tuple[str, str]:
    """The project's recorded release commit and role checksum, empty if unpinned."""

    def read(name: str) -> str:
        path = working_dir / name
        return path.read_text().strip() if path.exists() else ""

    return read("shared-roles.ref"), read("shared-roles.sha256")


def _remote_tags(working_dir: Path, repository: str) -> dict[str, str]:
    """Tag name → commit of the shared repository, from the first reachable URL."""
    for url in _candidate_repo_urls(working_dir, repository=repository):
        result = subprocess.run(
            ["git", "ls-remote", "--tags", url],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            continue
        tags: dict[str, str] = {}
        for line in result.stdout.splitlines():
            commit, _, ref = line.partition("\t")
            name = ref.removeprefix("refs/tags/")
            # An annotated tag lists its commit as `<name>^{}`; prefer that line.
            if name.endswith("^{}"):
                tags[name[:-3]] = commit
            else:
                tags.setdefault(name, commit)
        return tags
    raise click.ClickException(f"Could not read the releases of {repository}.")


def latest_published_release(repository: str) -> tuple[str, str]:
    """The newest ``vX.Y.Z`` release of ``repository`` and its commit."""
    tags = _remote_tags(Path.cwd(), repository)
    release = latest_release(list(tags))
    if not release:
        raise click.ClickException(f"{repository} has no release yet.")
    return release, tags[release]


def _requested_release(working_dir: Path, repository: str, version: str | None) -> str:
    """The named release or commit, or the latest release of the shared repository."""
    if version and COMMIT.fullmatch(version):
        return version
    tags = _remote_tags(working_dir, repository)
    if version:
        if version not in tags:
            raise click.ClickException(
                f"{repository} has no release {version}. "
                "Releases are published there with `mise run release vX.Y.Z`."
            )
        return version
    legacy = legacy_copy(working_dir)
    if legacy and repository == legacy:
        raise click.ClickException(
            f"This project still calls the owner copy {legacy}.\n"
            f"Move it to {DEFAULT_SHARED_REPOSITORY} once with: "
            "startup migrate deploy"
        )
    release = latest_release(list(tags))
    if not release:
        raise click.ClickException(f"{repository} has no release yet.")
    return release


def _report_check(working_dir: Path, repository: str, release: str) -> None:
    current, _checksum = _pin(working_dir)
    tags = {} if COMMIT.fullmatch(release) else _remote_tags(working_dir, repository)
    if current and current in {release, tags.get(release)}:
        click.echo(f"Up to date: this project runs release {release}.")
        return
    click.echo(f"Update available: release {release} of {repository}.")
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


def run_update(working_directory: str, *, version: str | None, check: bool) -> None:
    working_dir = _resolve_working_dir(working_directory)
    repository = shared_repository(working_dir)
    if version == LATEST:
        follow_latest(working_dir, repository, check=check)
        return
    if follows_latest(working_dir) and not version:
        report_following(repository)
        return
    release = _requested_release(working_dir, repository, version)
    if check:
        _report_check(working_dir, repository, release)
        return
    before = _pin(working_dir)
    stop_following(working_dir)
    moved = run_pin(
        working_directory=working_directory, version=release, repository=repository
    )
    _report_update(before, working_dir, release, moved)
