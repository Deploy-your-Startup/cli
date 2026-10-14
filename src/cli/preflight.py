"""Read-only CLI setup checks and optional server deployment prerequisites."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import click

from cli import wizard_output as ui
from cli.ssh_keys import public_key_is_valid


def local_checks(
    *,
    browser: bool = True,
    server: bool = True,
    ssh_key: bool = True,
    public_key_path: Path | None = None,
) -> list[tuple[bool, str]]:
    checks = []
    if server:
        checks.append(
            (sys.platform == "darwin", "macOS (full-stack bootstrap uses Keychain)")
        )
    for name in ("git", "gh", "uv", "ssh-keygen") if server else ("git", "gh", "uv"):
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
    if server and ssh_key:
        keys = (
            [public_key_path]
            if public_key_path
            else [
                Path.home() / ".ssh" / name for name in ("id_ed25519.pub", "id_rsa.pub")
            ]
        )
        checks.append(
            (
                any(public_key_is_valid(path) for path in keys),
                "SSH public key for server access (--ssh-public-key <path.pub> or ssh-keygen -t ed25519)",
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


def check_local_prerequisites(*, browser: bool = True, ssh_key: bool = True) -> None:
    missing = [
        label for ok, label in local_checks(browser=browser, ssh_key=ssh_key) if not ok
    ]
    if missing:
        raise click.ClickException(
            "Before bootstrap, resolve:\n"
            + "\n".join(f"  • {label}" for label in missing)
            + "\nRun startup doctor --provider hetzner or --provider byos for deployment checks."
        )


@click.command()
@click.option(
    "--provider",
    type=click.Choice(["hetzner", "byos"]),
    help="Also check server deployment prerequisites for this provider.",
)
@click.option(
    "--ssh-public-key",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Public key to check instead of the default SSH key paths.",
)
@click.option(
    "--project-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="Check project legal/consent configuration instead of machine prerequisites.",
)
def doctor(provider=None, ssh_public_key=None, project_dir: Path | None = None):
    """Check CLI setup, a deployment target, or a project's policy."""
    if project_dir is not None:
        if provider is not None or ssh_public_key is not None:
            raise click.UsageError(
                "Use --project-dir separately from --provider and --ssh-public-key."
            )
        from cli.site_policy import check_project_policy

        check_project_policy(project_dir.resolve())
        return
    if ssh_public_key and provider is None:
        raise click.UsageError(
            "Use --ssh-public-key with --provider hetzner or --provider byos."
        )
    checks = local_checks(
        browser=provider == "hetzner",
        server=provider is not None,
        public_key_path=ssh_public_key,
    )
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
    if provider == "hetzner":
        click.echo(
            "For this target, you need a Hetzner Cloud account and a domain whose DNS you can edit. Infrastructure is billed to your account."
        )
    elif provider == "byos":
        click.echo(
            "For this target, you need SSH access to your existing server and a domain whose DNS you can edit. No Hetzner account is required."
        )
    if not all(ok for ok, _ in checks):
        raise click.ClickException(
            "Prerequisites are incomplete; no resources were created."
        )
