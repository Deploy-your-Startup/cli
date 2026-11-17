"""Adopt a released project router, retaining old releases as rollback targets."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import click
import yaml

from cli.project_version import WORKFLOW, project_version
from cli.project_workflow import consolidated_workflows, legacy_workflows
from cli.workflow_pins import pin_workflow_references


def adopt_workflow_version(
    working_dir: Path, repository: str, revision: str, label: str
) -> int | None:
    """Inspect the actual release before migrating a workflow-only project."""
    from cli.ansible_commands import clone_or_update_shared_roles

    with tempfile.TemporaryDirectory(
        prefix=".startup-version-", dir=working_dir
    ) as temporary:
        root = clone_or_update_shared_roles(
            working_directory=temporary, version=revision, repository=repository
        )
        return adopt_project_version(
            working_dir, root, repository, revision, label, workflows=True
        )


def _schema(root: Path, router: str) -> dict | None:
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"HEAD:.github/workflows/{router}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return None
    return yaml.safe_load(result.stdout)


def adopt_project_version(
    working_dir: Path,
    root: Path,
    repository: str,
    revision: str,
    label: str,
    *,
    workflows: bool,
) -> int | None:
    """Return None for an older release, otherwise migrate with one authoritative ref."""
    project = working_dir.parent
    if not workflows and project_version(project, validate_calls=False):
        raise click.ClickException(
            "This project uses one deployment version; --roles-only cannot split it from CI."
        )
    router = "project-pages.yml" if working_dir.name == ".github" else "project.yml"
    schema = _schema(root, router)
    if schema is None:
        version = project_version(project, validate_calls=False)
        if version:
            updates = legacy_workflows(project, repository, revision)
            for path, content in updates.items():
                path.write_text(content)
            (project / WORKFLOW).unlink()
            click.echo(
                "Rolling back to a legacy release; its separate role pins are required again."
            )
        return None
    if not workflows:
        raise click.ClickException(
            "This release uses one deployment version; --roles-only cannot split it from CI."
        )
    updates, moved = consolidated_workflows(
        project, repository, router, revision, schema
    )
    for path, content in updates.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    moved += pin_workflow_references(project, repository, revision, label)
    for name in ("shared-roles.ref", "shared-roles.sha256", "shared-roles.latest"):
        (working_dir / name).unlink(missing_ok=True)
    click.echo(f"Deployment version: {revision} in {WORKFLOW}. No deployment was run.")
    return moved
