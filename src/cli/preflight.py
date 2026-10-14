"""Read-only checks for the supported macOS full-stack onboarding path."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import click

from cli import wizard_output as ui


def local_checks(*, browser: bool = True) -> list[tuple[bool, str]]:
    checks = [(sys.platform == "darwin", "macOS (full-stack bootstrap uses Keychain)")]
    for name in ("git", "gh", "uv", "ssh-keygen"):
        checks.append((shutil.which(name) is not None, f"{name} executable on PATH"))
    if shutil.which("git"):
        result = subprocess.run(
            ["git", "var", "GIT_AUTHOR_IDENT"], capture_output=True, check=False
        )
        checks.append(
            (
                result.returncode == 0,
                "Git author configuration (git config --global user.name / user.email)",
            )
        )
    keys = [Path.home() / ".ssh" / name for name in ("id_ed25519.pub", "id_rsa.pub")]
    checks.append(
        (
            any(path.is_file() for path in keys),
            "SSH public key (ssh-keygen -t ed25519)",
        )
    )
    if browser:
        paths = [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ]
        checks.append(
            (
                any(path.is_file() for path in paths),
                "Google Chrome for browser setup",
            )
        )
    return checks


def check_local_prerequisites(*, browser: bool = True) -> None:
    missing = [label for ok, label in local_checks(browser=browser) if not ok]
    if missing:
        raise click.ClickException(
            "Before bootstrap, resolve:\n"
            + "\n".join(f"  • {label}" for label in missing)
            + "\nRun startup doctor for the complete check."
        )


@click.command()
@click.option(
    "--project-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Check project legal/consent configuration instead of machine prerequisites.",
)
def doctor(project_dir: Path | None = None):
    """Check setup prerequisites, or a project policy with --project-dir."""
    if project_dir is not None:
        from cli.site_policy import check_project_policy

        check_project_policy(project_dir.resolve())
        return
    checks = local_checks()
    if shutil.which("gh"):
        status = subprocess.run(
            ["gh", "auth", "status"], capture_output=True, text=True, check=False
        )
        checks.append(
            (
                status.returncode == 0,
                "GitHub login (gh auth login --git-protocol https --scopes repo,workflow,read:packages,write:packages)",
            )
        )
        if status.returncode == 0:
            output = status.stdout + status.stderr
            scopes = "'workflow'" in output and (
                "'read:packages'" in output or "'write:packages'" in output
            )
            checks.append(
                (
                    scopes,
                    "GitHub workflow + package scopes (gh auth refresh -h github.com -s repo,workflow,read:packages,write:packages)",
                )
            )
    for ok, label in checks:
        if ok:
            ui.success(label)
        else:
            ui.error(f"Missing: {label}")
    click.echo(
        "You also need a Hetzner Cloud account and a domain whose DNS you can edit. Infrastructure is billed to your account."
    )
    if not all(ok for ok, _ in checks):
        raise click.ClickException(
            "Prerequisites are incomplete; no resources were created."
        )
