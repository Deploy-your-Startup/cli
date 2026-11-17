"""Keep a project's shared workflow and action references on its role pin's revision."""

from __future__ import annotations

import re
from pathlib import Path


def workflow_revisions(project_root: Path, repository: str) -> set[str]:
    """All shared workflow/action revisions actually used by a project."""
    from cli.shared_source import workflow_files

    pattern = re.compile(
        r"uses:\s*" + re.escape(repository) + r"/\.github/[\w./-]+@([^\s#]+)"
    )
    return {
        revision
        for path in workflow_files(project_root)
        for revision in pattern.findall(path.read_text(encoding="utf-8"))
    }


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


def move_workflow_references(
    project_root: Path, repositories: set[str], target: str
) -> int:
    """Point references to any of ``repositories`` at ``target``, keeping their revision.

    Returns the number of references that changed.
    """
    names = "|".join(re.escape(name) for name in sorted(repositories))
    pattern = re.compile(r"(uses:\s*)(?:" + names + r")(/\.github/)")
    changed = 0
    workflows = project_root / ".github" / "workflows"
    for path in sorted(workflows.glob("*.y*ml")) if workflows.is_dir() else []:
        content = path.read_text(encoding="utf-8")
        updated, count = pattern.subn(rf"\g<1>{target}\g<2>", content)
        if count:
            path.write_text(updated, encoding="utf-8")
            changed += count
    return changed


BUILD_PERMISSIONS = (
    "permissions:",
    "  contents: read",
    "  actions: read",
    "  pull-requests: write",
    "  packages: write",
    "  id-token: write",
)
_BUILD_CALL = re.compile(
    r"^(?P<indent>[ \t]+)uses:\s*(?P<repository>[\w.-]+/[\w.-]+)"
    r"/\.github/workflows/build-and-deploy-service(?:-parallel)?\.ya?ml@"
)


def grant_build_permissions(project_root: Path, repository: str) -> int:
    """Grant each job that calls ``repository``'s shared build what its nested jobs request.

    GitHub does not extend ``id-token: write`` and the other nested permissions
    to a reusable workflow of another owner, so such a run fails to start. Jobs
    that already declare ``permissions`` are left alone. Returns the number of
    jobs that changed.
    """
    granted = 0
    workflows = project_root / ".github" / "workflows"
    for path in sorted(workflows.glob("*.y*ml")) if workflows.is_dir() else []:
        lines = path.read_text(encoding="utf-8").split("\n")
        output: list[str] = []
        for index, line in enumerate(lines):
            output.append(line)
            match = _BUILD_CALL.match(line)
            if not match or match.group("repository") != repository:
                continue
            indent = match.group("indent")
            if not any(
                other.startswith(indent + "permissions:")
                for other in _job_lines(lines, index, indent)
            ):
                output += [indent + entry for entry in BUILD_PERMISSIONS]
                granted += 1
        if len(output) != len(lines):
            path.write_text("\n".join(output), encoding="utf-8")
    return granted


def _job_lines(lines: list[str], index: int, indent: str) -> list[str]:
    """The lines of the job whose ``uses:`` is at ``index``, at that indentation."""

    def inside(line: str) -> bool:
        return not line.strip() or line.startswith(indent)

    start = index
    while start > 0 and inside(lines[start - 1]):
        start -= 1
    end = index + 1
    while end < len(lines) and inside(lines[end]):
        end += 1
    return lines[start:end]
