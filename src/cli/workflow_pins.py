"""Keep a project's shared workflow and action references on its role pin's revision."""

from __future__ import annotations

import re
from pathlib import Path


def pin_workflow_references(
    project_root: Path, repository: str, revision: str, label: str
) -> int:
    """Point every reference to the owner repository's workflows and actions at ``revision``.

    Returns the number of references that changed.
    """
    pattern = re.compile(
        r"(uses:\s*"
        + re.escape(repository)
        + r"/\.github/(?:workflows/[\w.-]+\.ya?ml|actions/[\w.-]+))@[^\s#]+(?:[ \t]+#[^\n]*)?"
    )
    # Name the release next to its commit; a branch name would go stale.
    comment = "" if label in {revision, "main"} else f" # {label}"
    target = f"@{revision}{comment}"
    changed = 0

    def repin(match: re.Match[str]) -> str:
        nonlocal changed
        pinned = match.group(1) + target
        changed += pinned != match.group(0)
        return pinned

    workflows = project_root / ".github" / "workflows"
    for path in sorted(workflows.glob("*.y*ml")) if workflows.is_dir() else []:
        content = path.read_text(encoding="utf-8")
        updated = pattern.sub(repin, content)
        if updated != content:
            path.write_text(updated, encoding="utf-8")
    return changed
