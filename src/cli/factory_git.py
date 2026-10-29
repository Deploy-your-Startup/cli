"""Keep a Factory in its own Git repository and, on request, a private GitHub one."""

from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

from cli import wizard_output as ui


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=root, capture_output=True, text=True, check=check
    )


def gh(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True, check=False)


def own_repository(root: Path) -> bool:
    """True only when the Factory folder is the top of its own work tree."""
    result = git(root, "rev-parse", "--show-toplevel", check=False)
    return result.returncode == 0 and Path(result.stdout.strip()) == root.resolve()


def has_commit(root: Path) -> bool:
    return git(root, "rev-parse", "--verify", "-q", "HEAD", check=False).returncode == 0


def has_identity(root: Path) -> bool:
    return all(
        git(root, "config", key, check=False).stdout.strip()
        for key in ("user.name", "user.email")
    )


def github_login() -> str:
    result = gh("api", "--hostname", "github.com", "user", "-q", ".login")
    return result.stdout.strip() if result.returncode == 0 else ""


def use_github_identity(root: Path) -> bool:
    """Commit as the connected GitHub account when Git has no identity yet."""
    result = gh("api", "--hostname", "github.com", "user", "-q", "[.id, .login] | @tsv")
    if result.returncode or "\t" not in result.stdout:
        return False
    user_id, login = result.stdout.strip().split("\t")
    git(root, "config", "user.name", login)
    git(root, "config", "user.email", f"{user_id}+{login}@users.noreply.github.com")
    return True


def confirm_existing_commit(root: Path) -> bool:
    ui.info("This Factory is not a Git repository yet.")
    if ui.confirm(
        "Commit its files (except those in .gitignore) to a new repository?",
        default=True,
    ):
        git(root, "init", "-b", "main")
        return True
    ui.info(f"Skipped. Later: git -C {shlex.quote(str(root))} init")
    return False


def commit_factory(root: Path, existing: bool) -> None:
    """Make the Factory its own repository with one commit of all unignored files."""
    if own_repository(root):
        if has_commit(root):
            return
    elif existing and not confirm_existing_commit(root):
        return
    else:
        git(root, "init", "-b", "main")
    # A rerun finishes a repository whose first commit lacked an author.
    git(root, "add", "-A")
    if not has_identity(root) and not use_github_identity(root):
        ui.warning(
            "Git has no author yet. Set git config --global user.name and "
            "user.email, then rerun this command to commit the Factory files."
        )
        return
    git(root, "commit", "-q", "-m", "chore: create Factory")
    ui.success("Factory files committed to a new Git repository.")


def has_origin(root: Path) -> bool:
    return git(root, "remote", "get-url", "origin", check=False).returncode == 0


def repository_name(root: Path) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", root.name).strip("-.") or "startup-factory"


def manual_publish_hint(root: Path, repository: str) -> str:
    folder = shlex.quote(str(root))
    return f"gh repo create {repository} --private --source {folder} --push"


def publish_to_github(root: Path) -> None:
    """Offer a private GitHub repository; never block onboarding on failure."""
    if not own_repository(root) or not has_commit(root):
        ui.info("GitHub repository skipped: commit the Factory files first.")
        return
    if has_origin(root):
        ui.info("GitHub repository: origin is already configured.")
        return
    owner = github_login()
    repository = f"{owner}/{repository_name(root)}"
    if not owner:
        ui.info(
            "GitHub is not connected; your Factory stays local. Later: gh auth login, "
            "then " + manual_publish_hint(root, repository_name(root))
        )
        return
    if not ui.confirm(
        f"Back up this Factory to private GitHub repository {repository}?"
    ):
        ui.info(
            "Your Factory stays local. Later: " + manual_publish_hint(root, repository)
        )
        return
    create_repository(root, repository)


def create_repository(root: Path, repository: str) -> None:
    view = gh("repo", "view", repository, "--json", "visibility")
    if view.returncode == 0:
        visibility = json.loads(view.stdout or "{}").get("visibility", "unknown")
        ui.warning(
            f"{repository} already exists ({visibility.lower()}); nothing was changed. "
            f"Connect it yourself: git -C {shlex.quote(str(root))} remote add origin "
            f"https://github.com/{repository}.git"
        )
        return
    ui.action_start("Creating private GitHub repository...")
    created = gh(
        "repo", "create", repository, "--private", "--source", str(root), "--push"
    )
    if created.returncode:
        ui.action_fail("GitHub repository not created")
        ui.warning(
            (created.stderr.strip() or "gh repo create failed")
            + ". Retry: "
            + manual_publish_hint(root, repository)
        )
        return
    ui.action_done(f"https://github.com/{repository}")


def commit_registration(root: Path, name: str) -> None:
    """Record a newly registered startup in the Factory's history when it has one."""
    if not own_repository(root) or not has_commit(root):
        return
    committed = git(
        root,
        "commit",
        "-q",
        "-m",
        f"chore: register {name}",
        "--",
        "projects.yaml",
        check=False,
    )
    if committed.returncode:
        return
    if git(root, "rev-parse", "-q", "--verify", "@{u}", check=False).returncode == 0:
        git(root, "push", "-q", check=False)
