"""Publish each sync as an immutable release tag of the owner repository.

GitHub resolves ``uses:`` references literally, so a shared workflow cannot
name "my own revision" for the actions it calls. The template therefore refers
to the owner copy's ``main``. Sync rewrites those references to a tag that it
creates on the very commit it publishes: a release is self-contained, and a
later sync cannot change what a project pinned to an earlier release runs.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import click


def _git(root: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=root, text=True, capture_output=True, check=False
    )
    if check and result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise click.ClickException(f"Command failed: git {' '.join(args)}\n{details}")
    return result.stdout.strip() if result.returncode == 0 else ""


def release_tag(source_root: Path) -> str:
    """Name the release after the source: its version tag, else its commit."""
    version = _git(
        source_root, "describe", "--tags", "--exact-match", "HEAD", check=False
    )
    if re.fullmatch(r"v\d+(\.\d+)*", version):
        return version
    return "source-" + _git(source_root, "rev-parse", "--short=12", "HEAD")


def pin_self_references(target_root: Path, repository: str, tag: str) -> int:
    """Point the owner copy's references to its own ``main`` at the release tag."""
    pattern = re.compile(
        r"(uses:\s*" + re.escape(repository) + r"/\.github/[\w./-]+)@main\b"
    )
    rewritten = 0
    for path in sorted((target_root / ".github").rglob("*.yml")):
        content = path.read_text(encoding="utf-8")
        updated, count = pattern.subn(r"\1@" + tag, content)
        if count:
            path.write_text(updated, encoding="utf-8")
            rewritten += count
    return rewritten


def _published_tree(target_root: Path, tag: str) -> str:
    return _git(target_root, "rev-parse", f"refs/tags/{tag}^{{tree}}", check=False)


def _commit(target_root: Path, message: str) -> None:
    """Commit the staged release unless the checkout already holds it."""
    if _git(target_root, "status", "--short"):
        _git(target_root, "commit", "-m", message)


def publish_release(
    target_root: Path, *, tag: str, message: str, branch: str, move_branch: bool
) -> bool:
    """Commit the rendered release, tag it and push; never move an existing tag."""
    _git(target_root, "add", "-A")
    tree = _git(target_root, "write-tree")
    published = _published_tree(target_root, tag)
    if published and published != tree:
        raise click.ClickException(
            f"Release {tag} already exists with different contents. "
            "Releases are immutable; sync a different source version."
        )
    if published and not move_branch:
        _report(tag, published=True, moved=False)
        return False
    if not move_branch:
        _git(target_root, "checkout", "--detach")
    branch_changed = bool(_git(target_root, "status", "--short"))
    _commit(target_root, message)
    if not published:
        _git(target_root, "tag", tag)
        _git(target_root, "push", "origin", f"refs/tags/{tag}")
    if move_branch and branch_changed:
        # Keep the user's gh-selected protocol; see ``_clone_target_repo``.
        _git(target_root, "push", "origin", branch)
    _report(tag, published=bool(published), moved=move_branch and branch_changed)
    return not published or (move_branch and branch_changed)


def _report(tag: str, *, published: bool, moved: bool) -> None:
    if published and not moved:
        click.echo(f"Already up to date: release {tag} is published.")
        return
    click.echo(
        f"main now holds release {tag}." if published else f"Published release {tag}."
    )
    click.echo(
        "Existing projects keep their pins. Adopt the release per project with:\n"
        f"  startup ansible pin --working-directory deployment --version {tag}"
    )
