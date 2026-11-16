"""Keep a project's shared workflow references on the same revision as its role pin."""

from __future__ import annotations

import re
from pathlib import Path


def pin_workflow_references(
    project_root: Path, repository: str, revision: str, label: str
) -> int:
    """Point every reference to the owner repository's workflows at ``revision``."""
    pattern = re.compile(
        r"(uses:\s*"
        + re.escape(repository)
        + r"/\.github/workflows/[\w.-]+\.ya?ml)@[^\s#]+(?:[ \t]+#[^\n]*)?"
    )
    # Name the release next to its commit; a branch name would go stale.
    comment = "" if label in {revision, "main"} else f" # {label}"
    rewritten = 0
    workflows = project_root / ".github" / "workflows"
    for path in sorted(workflows.glob("*.y*ml")) if workflows.is_dir() else []:
        content = path.read_text(encoding="utf-8")
        updated, count = pattern.subn(rf"\1@{revision}{comment}", content)
        if updated != content:
            path.write_text(updated, encoding="utf-8")
        rewritten += count
    return rewritten
