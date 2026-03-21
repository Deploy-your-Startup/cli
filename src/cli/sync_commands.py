"""Commands for syncing shared repositories into a user's GitHub account."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import os
from pathlib import Path

import click


DEFAULT_BRANCH = "main"
DEFAULT_TEMPLATE_OWNER = "Deploy-your-Startup"
DEFAULT_DEPLOY_TEMPLATE_REPO = "deploy-template"


def _run_command(
    command: list[str],
    *,
    cwd: Path,
    capture_output: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    command_env = os.environ.copy()
    command_env.setdefault("GH_PAGER", "cat")
    command_env.setdefault("PAGER", "cat")
    command_env.setdefault("GIT_PAGER", "cat")
    try:
        return subprocess.run(
            command,
            cwd=str(cwd),
            text=True,
            env=command_env,
            capture_output=capture_output,
            check=check,
        )
    except subprocess.CalledProcessError as exc:
        details = (exc.stderr or exc.stdout or "").strip()
        rendered = " ".join(command)
        if details:
            raise click.ClickException(
                f"Command failed: {rendered}\n{details}"
            ) from exc
        raise click.ClickException(f"Command failed: {rendered}") from exc


def _github_owner(owner: str | None) -> str:
    if owner:
        return owner

    result = _run_command(
        ["gh", "api", "user", "-q", ".login"],
        cwd=Path.cwd(),
        capture_output=True,
    )
    resolved_owner = result.stdout.strip()
    if not resolved_owner:
        raise click.ClickException("Could not determine GitHub username from gh auth.")
    return resolved_owner


def _github_owner_type(owner: str) -> str:
    result = _run_command(
        ["gh", "api", f"users/{owner}", "-q", ".type"],
        cwd=Path.cwd(),
        capture_output=True,
    )
    owner_type = result.stdout.strip()
    if owner_type not in {"User", "Organization"}:
        raise click.ClickException(
            f"Unsupported GitHub owner type for {owner}: {owner_type}"
        )
    return owner_type


def _repo_exists(full_repo_name: str) -> bool:
    result = _run_command(
        ["gh", "repo", "view", full_repo_name],
        cwd=Path.cwd(),
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def _github_repo_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}.git"


def _ensure_repo_exists(
    full_repo_name: str,
    *,
    private: bool,
    description: str,
    cwd: Path,
) -> None:
    if _repo_exists(full_repo_name):
        return

    visibility_flag = "--private" if private else "--public"
    click.echo(f"Creating GitHub repository {full_repo_name} ...")
    _run_command(
        [
            "gh",
            "repo",
            "create",
            full_repo_name,
            visibility_flag,
            "--description",
            description,
            "--add-readme",
        ],
        cwd=cwd,
    )


def _set_actions_access(full_repo_name: str, access_level: str, *, cwd: Path) -> None:
    _run_command(
        [
            "gh",
            "api",
            "--silent",
            "-X",
            "PUT",
            f"repos/{full_repo_name}/actions/permissions/access",
            "-f",
            f"access_level={access_level}",
        ],
        cwd=cwd,
    )


def _clone_source_repo(source_repo: str, destination: Path, branch: str) -> None:
    _run_command(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            branch,
            source_repo,
            str(destination),
        ],
        cwd=destination.parent,
    )


def _clone_target_repo(full_repo_name: str, destination: Path, branch: str) -> None:
    _run_command(
        ["gh", "repo", "clone", full_repo_name, str(destination)],
        cwd=destination.parent,
    )

    branches = _run_command(
        ["git", "branch", "--list", branch],
        cwd=destination,
        capture_output=True,
    ).stdout.strip()
    if branches:
        _run_command(["git", "checkout", branch], cwd=destination)
    else:
        _run_command(["git", "checkout", "-b", branch], cwd=destination)


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    else:
        path.unlink()


def _clear_target_repo(target_root: Path) -> None:
    for entry in target_root.iterdir():
        if entry.name == ".git":
            continue
        _remove_path(entry)


def _copy_entry(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def _copy_repo_contents(source_root: Path, target_root: Path) -> None:
    for entry in source_root.iterdir():
        if entry.name == ".git":
            continue
        _copy_entry(entry, target_root / entry.name)


def _copy_selected_paths(
    source_root: Path, target_root: Path, paths: list[str]
) -> None:
    for relative_path in paths:
        source_path = source_root / relative_path
        if not source_path.exists():
            continue
        _copy_entry(source_path, target_root / relative_path)


def _replace_placeholders(target_root: Path, replacements: dict[str, str]) -> None:
    if not replacements:
        return

    for path in target_root.rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue

        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue

        updated = content
        for placeholder, value in replacements.items():
            updated = updated.replace(placeholder, value)

        if updated != content:
            path.write_text(updated, encoding="utf-8")


def _commit_and_push(target_root: Path, message: str) -> bool:
    status = _run_command(
        ["git", "status", "--short"], cwd=target_root, capture_output=True
    ).stdout.strip()
    if not status:
        click.echo("Already up to date.")
        return False

    _run_command(["git", "add", "-A"], cwd=target_root)
    _run_command(["git", "commit", "-m", message], cwd=target_root)
    _run_command(["git", "push", "origin", DEFAULT_BRANCH], cwd=target_root)
    return True


def _sync_repo(
    *,
    source_repo: str,
    target_repo: str,
    private: bool,
    description: str,
    commit_message: str,
    branch: str = DEFAULT_BRANCH,
    sync_paths: list[str] | None = None,
    dry_run: bool = False,
    replacements: dict[str, str] | None = None,
    actions_access_level: str | None = None,
) -> bool:
    with tempfile.TemporaryDirectory(prefix="startup-sync-") as temp_dir:
        temp_root = Path(temp_dir)
        source_root = temp_root / "source"
        target_root = temp_root / "target"

        if not _repo_exists(target_repo):
            if dry_run:
                click.echo(f"Would create GitHub repository {target_repo}.")
                if actions_access_level and private:
                    click.echo(
                        f"Would set GitHub Actions access for {target_repo} to '{actions_access_level}'."
                    )
                return True

            _ensure_repo_exists(
                target_repo, private=private, description=description, cwd=temp_root
            )

        if private and actions_access_level:
            if dry_run:
                click.echo(
                    f"Would set GitHub Actions access for {target_repo} to '{actions_access_level}'."
                )
            else:
                click.echo(
                    f"Configuring GitHub Actions access for {target_repo} ({actions_access_level}) ..."
                )
                _set_actions_access(target_repo, actions_access_level, cwd=temp_root)

        click.echo(f"Cloning source repository {source_repo} ...")
        _clone_source_repo(source_repo, source_root, branch)

        click.echo(f"Cloning target repository {target_repo} ...")
        _clone_target_repo(target_repo, target_root, branch)

        _clear_target_repo(target_root)
        if sync_paths:
            _copy_selected_paths(source_root, target_root, sync_paths)
        else:
            _copy_repo_contents(source_root, target_root)

        _replace_placeholders(target_root, replacements or {})

        if dry_run:
            status = _run_command(
                ["git", "status", "--short"], cwd=target_root, capture_output=True
            ).stdout.strip()
            if status:
                click.echo(status)
                click.echo("Dry-run only, no commit or push performed.")
                return True
            click.echo("Already up to date.")
            return False

        changed = _commit_and_push(target_root, commit_message)
        if changed:
            click.echo(f"Synced {target_repo} successfully.")
        return changed


def sync_deploy_repo(
    *,
    owner: str | None = None,
    repo_name: str = "deploy-your-startup",
    source_owner: str = DEFAULT_TEMPLATE_OWNER,
    source_repo: str = DEFAULT_DEPLOY_TEMPLATE_REPO,
    private: bool = True,
    dry_run: bool = False,
) -> bool:
    resolved_owner = _github_owner(owner)
    owner_type = _github_owner_type(resolved_owner)
    return _sync_repo(
        source_repo=_github_repo_url(source_owner, source_repo),
        target_repo=f"{resolved_owner}/{repo_name}",
        private=private,
        description="Shared deploy workflows, actions, and Ansible roles",
        commit_message="sync shared deploy repo",
        dry_run=dry_run,
        replacements={
            "§§deploy_your_startup.github_username§§": resolved_owner,
            "§§deploy_your_startup.deploy_repo_name§§": repo_name,
        },
        actions_access_level=(
            "organization"
            if private and owner_type == "Organization"
            else "user"
            if private
            else None
        ),
    )


def sync_ci_actions(
    *,
    owner: str | None = None,
    repo_name: str = "deploy-your-startup",
    roles_repo_name: str = "ansible-roles",
    source_owner: str = DEFAULT_TEMPLATE_OWNER,
    source_repo: str = DEFAULT_DEPLOY_TEMPLATE_REPO,
    private: bool = True,
    dry_run: bool = False,
) -> bool:
    return sync_deploy_repo(
        owner=owner,
        repo_name=repo_name,
        source_owner=source_owner,
        source_repo=source_repo,
        private=private,
        dry_run=dry_run,
    )


def sync_roles(
    *,
    owner: str | None = None,
    repo_name: str = "deploy-your-startup",
    source_owner: str = DEFAULT_TEMPLATE_OWNER,
    source_repo: str = DEFAULT_DEPLOY_TEMPLATE_REPO,
    private: bool = True,
    dry_run: bool = False,
) -> bool:
    return sync_deploy_repo(
        owner=owner,
        repo_name=repo_name,
        source_owner=source_owner,
        source_repo=source_repo,
        private=private,
        dry_run=dry_run,
    )


def sync_all(
    *,
    owner: str | None = None,
    ci_repo_name: str = "deploy-your-startup",
    roles_repo_name: str = "deploy-your-startup",
    ci_source_owner: str = DEFAULT_TEMPLATE_OWNER,
    ci_source_repo: str = DEFAULT_DEPLOY_TEMPLATE_REPO,
    roles_source_owner: str = DEFAULT_TEMPLATE_OWNER,
    roles_source_repo: str = DEFAULT_DEPLOY_TEMPLATE_REPO,
    private: bool = True,
    dry_run: bool = False,
) -> None:
    sync_deploy_repo(
        owner=owner,
        repo_name=ci_repo_name,
        source_owner=ci_source_owner,
        source_repo=ci_source_repo,
        private=private,
        dry_run=dry_run,
    )
