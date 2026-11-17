"""The project's one deployment version is a native reusable-workflow reference.

GitHub requires a literal ``uses:`` revision. The local workflow is therefore
the configuration, rather than a second version file that CI must duplicate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import click
import yaml

WORKFLOW = ".github/workflows/startup.yml"
ROUTERS = ("project.yml", "project-pages.yml")
REFERENCE = re.compile(
    r"uses:\s*(?P<repository>[\w.-]+/[\w.-]+)/\.github/workflows/"
    r"(?P<workflow>project(?:-pages)?\.yml)@(?P<revision>[^\s#]+)"
)


@dataclass(frozen=True)
class DeploymentVersion:
    repository: str
    revision: str
    workflow: str


def _uses(node: object) -> list[str]:
    if isinstance(node, list):
        return [ref for child in node for ref in _uses(child)]
    if not isinstance(node, dict):
        return []
    references = [str(node["uses"])] if "uses" in node else []
    return references + [ref for child in node.values() for ref in _uses(child)]


def _references(path: Path) -> list[str]:
    try:
        return _uses(yaml.safe_load(path.read_text()))
    except yaml.YAMLError as exc:
        raise click.ClickException(f"{path} is not a valid workflow.") from exc


def project_version(
    project: Path, *, validate_calls: bool = True
) -> DeploymentVersion | None:
    """Read the canonical reference; reject any additional shared version.

    Absence means an older project, which keeps its legacy pin until an explicit
    update migrates it. A malformed canonical file must never fall back to main.
    """
    path = project / WORKFLOW
    if not path.exists():
        return None
    references = _references(path)
    matches = [
        match for ref in references if (match := REFERENCE.fullmatch("uses: " + ref))
    ]
    if len(matches) != 1:
        raise click.ClickException(
            f"{path} must contain one shared project workflow reference."
        )
    match = matches[0]
    version = DeploymentVersion(**match.groupdict())
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", version.revision)
        or ".." in version.revision
    ):
        raise click.ClickException(f"Invalid deployment revision in {path}.")
    if not validate_calls:
        return version
    prefix = version.repository + "/.github/"
    if sum(ref.startswith(prefix) for ref in references) != 1:
        raise click.ClickException(
            f"{path} must contain only one shared deployment reference."
        )
    for other in sorted(path.parent.glob("*.y*ml")):
        if other != path and any(ref.startswith(prefix) for ref in _references(other)):
            raise click.ClickException(
                f"{other} still records a shared version. Run startup update to migrate it."
            )
    return version


def configured_revision(working_dir: Path) -> str | None:
    version = project_version(working_dir.parent)
    if version is None:
        return None
    old = working_dir / "shared-roles.ref"
    if old.exists() and old.read_text().strip() != version.revision:
        raise click.ClickException(
            "The old shared-roles.ref conflicts with .github/workflows/startup.yml. "
            "Review the versions before migrating with startup update."
        )
    return version.revision
