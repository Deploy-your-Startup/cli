"""Update workflow-only projects, including Cloudflare Pages, without Ansible files."""

from pathlib import Path

import click

from cli.project_follow import (
    follow_latest,
    follows_latest,
    report_following,
    stop_following,
)
from cli.shared_source import shared_repository
from cli.workflow_pins import pin_workflow_references, workflow_revisions


def run_workflow_update(project: Path, *, version: str | None, check: bool) -> None:
    """Use workflow references as the pin; .github holds an optional follow marker."""
    from cli.project_update import COMMIT, LATEST, _remote_tags, _requested_release

    metadata = project / ".github"
    repository = shared_repository(metadata)
    revisions = workflow_revisions(project, repository)
    if not revisions:
        raise click.ClickException(
            f"No shared deployment workflows from {repository} found in {project}."
        )
    if version == LATEST:
        follow_latest(metadata, repository, check=check)
        return
    if follows_latest(metadata) and not version:
        report_following(repository)
        return
    release = _requested_release(metadata, repository, version)
    revision = (
        release
        if COMMIT.fullmatch(release)
        else _remote_tags(metadata, repository)[release]
    )
    if check:
        if revisions == {revision}:
            click.echo(f"Up to date: configured deployment release {release}.")
        else:
            click.echo(f"Update available: release {release} of {repository}.")
            click.echo(f"Adopt it with: startup update --version {release}")
        return
    from cli.project_version_update import adopt_workflow_version

    moved = adopt_workflow_version(metadata, repository, revision, release)
    if moved is None:
        moved = pin_workflow_references(project, repository, revision, release)
    stop_following(metadata)
    if not moved:
        click.echo(f"Up to date: this project already targets release {release}.")
        return
    click.echo(
        f"This project now targets release {release} ({revision[:12]}); "
        f"{moved} shared workflow reference(s) moved.\n"
        "Nothing is deployed yet. Review the diff, commit and push to roll it out.\n"
        "To go back, run: startup update --version <previous release or commit>"
    )
