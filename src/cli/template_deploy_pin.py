"""Keep a project's shared deployment pin through ``startup template update``.

Copier answers record the shared deployment repository and ref of the original
render. ``startup update`` and ``startup migrate deploy`` move the project's
workflows and ``deployment/shared-roles.ref`` without touching those answers, so
an update would render the old ref again. The update therefore receives the
project's current repository and ref, and a conflict that only differs in the
release comment after that same reference keeps the project's line.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

FOLLOW_MARKER = "shared-roles.latest"
_CONFLICT = re.compile(
    r"<<<<<<< before updating\n(?P<before>.*?)=======\n(?P<after>.*?)>>>>>>> after updating\n",
    re.DOTALL,
)


def pinned_deployment_data(project: Path) -> dict[str, str]:
    """``deploy_repository`` and ``deploy_ref`` as the project uses them today."""
    from cli.shared_source import shared_repository

    deployment = project / "deployment"
    if not deployment.is_dir():
        return {}
    data = {"deploy_repository": shared_repository(deployment)}
    pin = deployment / "shared-roles.ref"
    if pin.is_file():
        data["deploy_ref"] = pin.read_text().strip()
    elif (deployment / FOLLOW_MARKER).exists():
        data["deploy_ref"] = "main"
    return data


def _shared_reference(line: str, repository: str) -> str | None:
    """``owner/name/.github/...@sha`` of a shared ``uses:`` line, without its comment."""
    match = re.match(
        r"\s*(?:- )?uses:\s*(" + re.escape(repository) + r"/\.github/\S+@\S+)",
        line,
    )
    return match.group(1) if match else None


def _same_references(before: str, after: str, repository: str) -> bool:
    before_lines, after_lines = before.splitlines(), after.splitlines()
    if len(before_lines) != len(after_lines):
        return False
    for old, new in zip(before_lines, after_lines, strict=True):
        if old == new:
            continue
        reference = _shared_reference(old, repository)
        if reference is None or reference != _shared_reference(new, repository):
            return False
    return True


def keep_pinned_references(project: Path, repository: str) -> list[str]:
    """Resolve workflow conflicts that only differ in a shared reference's comment.

    Returns the workflow files resolved; other conflicts are left for the owner.
    """
    unmerged = subprocess.run(
        ["git", "-C", str(project), "diff", "--name-only", "--diff-filter=U"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    resolved = []
    for name in unmerged:
        if not name.startswith(".github/workflows/"):
            continue
        path = project / name
        text = path.read_text(encoding="utf-8")
        conflicts = list(_CONFLICT.finditer(text))
        if not conflicts or not all(
            _same_references(c["before"], c["after"], repository) for c in conflicts
        ):
            continue
        path.write_text(_CONFLICT.sub(lambda c: c["before"], text), encoding="utf-8")
        # Mark it resolved, then leave it unstaged like every other updated file.
        for command in (["add", "--", name], ["reset", "-q", "--", name]):
            subprocess.run(["git", "-C", str(project), *command], check=True)
        resolved.append(name)
    return resolved
