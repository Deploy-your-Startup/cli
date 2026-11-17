"""Which repository supplies a project's shared roles, workflows and actions.

Projects pin releases of the public ``Deploy-your-Startup/deploy`` directly.
A fork is recorded in ``deployment/shared-roles.source``. Projects that still
call a per-owner ``<owner>/deploy-your-startup`` copy keep using it until
``startup migrate deploy`` moves them.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

DEFAULT_SHARED_REPOSITORY = "Deploy-your-Startup/deploy"
LEGACY_REPO_NAME = "deploy-your-startup"
SOURCE_FILE = "shared-roles.source"
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
RELEASE = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
_LEGACY_REFERENCE = re.compile(
    r"uses:\s*([A-Za-z0-9_.-]+)/" + re.escape(LEGACY_REPO_NAME) + r"/\.github/"
)


def workflow_files(project_root: Path) -> list[Path]:
    workflows = project_root / ".github" / "workflows"
    return sorted(workflows.glob("*.y*ml")) if workflows.is_dir() else []


def legacy_copy(working_dir: Path) -> str | None:
    """The owner copy this project's workflows still call, if any."""
    for path in workflow_files(working_dir.parent):
        match = _LEGACY_REFERENCE.search(path.read_text(encoding="utf-8"))
        if match:
            return f"{match.group(1)}/{LEGACY_REPO_NAME}"
    return None


def recorded_source(working_dir: Path) -> str | None:
    path = working_dir / SOURCE_FILE
    if not path.is_file():
        return None
    repository = path.read_text().strip()
    if not REPOSITORY.fullmatch(repository):
        raise ValueError(f"{path} must name one owner/repository")
    return repository


def shared_repository(working_dir: Path) -> str:
    """``owner/name`` of the repository this project's deployment comes from."""
    explicit = os.getenv("STARTUP_SHARED_REPOSITORY")
    if explicit:
        return explicit
    from cli.project_version import project_version

    version = project_version(working_dir.parent, validate_calls=False)
    if version:
        return version.repository
    return (
        recorded_source(working_dir)
        or legacy_copy(working_dir)
        or DEFAULT_SHARED_REPOSITORY
    )


def latest_release(tags: list[str]) -> str | None:
    """The highest ``vX.Y.Z`` among tag names."""
    releases = [tag for tag in tags if RELEASE.fullmatch(tag)]

    def version(tag: str) -> tuple[int, ...]:
        match = RELEASE.fullmatch(tag)
        assert match
        return tuple(int(part) for part in match.groups())

    return max(releases, key=version, default=None)
