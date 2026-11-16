"""Let a project follow the owner copy's ``main`` instead of a pinned release.

``startup update --version latest`` removes the project's pins. Its shared
workflows, actions and roles then come from the owner copy's ``main``, which
every ``startup sync`` moves: the project changes without a diff or review.
"""

from __future__ import annotations

from pathlib import Path

import click

from cli.ansible_commands import DEFAULT_SHARED_REPO_NAME, FOLLOW_MARKER
from cli.workflow_pins import pin_workflow_references

PIN_FILES = ("shared-roles.ref", "shared-roles.sha256")
MARKER_TEXT = (
    "This project follows the main branch of the owner's deploy-your-startup\n"
    "repository instead of a pinned release. Pin it again with:\n"
    "  startup update --version <release>\n"
)


def follows_latest(working_dir: Path) -> bool:
    return (working_dir / FOLLOW_MARKER).exists()


def stop_following(working_dir: Path) -> None:
    (working_dir / FOLLOW_MARKER).unlink(missing_ok=True)


def report_following() -> None:
    click.echo(
        "This project follows latest: it uses the main branch of your shared "
        "deployment repository,\nwhich changes with every startup sync. "
        "Pin it with: startup update --version <release>"
    )


def follow_latest(working_dir: Path, owner: str, *, check: bool) -> None:
    """Point the project at ``main`` and drop its pins."""
    if check:
        if follows_latest(working_dir):
            report_following()
        else:
            click.echo(
                "Would remove this project's pins and follow the main branch of "
                f"{owner}/{DEFAULT_SHARED_REPO_NAME}."
            )
        return
    moved = pin_workflow_references(
        working_dir.parent, f"{owner}/{DEFAULT_SHARED_REPO_NAME}", "main", "main"
    )
    removed = [name for name in PIN_FILES if (working_dir / name).exists()]
    for name in removed:
        (working_dir / name).unlink()
    (working_dir / FOLLOW_MARKER).write_text(MARKER_TEXT)
    click.echo(
        f"This project now follows latest: {moved} shared reference(s) point at "
        f"main and {len(removed)} pin file(s) were removed.\n"
        "From now on every startup sync changes what this project builds and "
        "deploys, without a diff in this repository.\n"
        "Review the diff, commit and push. Pin it again with: "
        "startup update --version <release>"
    )
