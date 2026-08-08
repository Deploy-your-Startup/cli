"""Pipeline runner: pick step list based on mode, check prereqs, run, summarize."""

from __future__ import annotations

import shutil
import subprocess

import click

from cli import wizard_output as ui

from .base import WizardStep
from .context import BootstrapContext
from .steps.byos import ByosStep
from .steps.cloudflare import CloudflareStep
from .steps.domain import DomainStep
from .steps.finalize import FinalizeStep
from .steps.hetzner import HetznerStep
from .steps.pitch_finalize import PitchFinalizeStep
from .steps.pitch_project import PitchProjectStep
from .steps.project import ProjectStep

FULLSTACK_STEPS: list[type[WizardStep]] = [
    DomainStep,
    HetznerStep,
    ProjectStep,
    FinalizeStep,
]
# Bring-your-own-server: no Hetzner token and no domain-registrar step — the user
# brings an existing VPS and points DNS at it themselves (explained in ByosStep).
BYOS_STEPS: list[type[WizardStep]] = [
    ByosStep,
    ProjectStep,
    FinalizeStep,
]
PITCH_STEPS: list[type[WizardStep]] = [
    CloudflareStep,
    DomainStep,
    PitchProjectStep,
    PitchFinalizeStep,
]


def steps_for(ctx: BootstrapContext) -> list[type[WizardStep]]:
    if ctx.kind == "pitch":
        return PITCH_STEPS
    return BYOS_STEPS if ctx.provider == "byos" else FULLSTACK_STEPS


def check_prerequisites(ctx: BootstrapContext) -> None:
    """Fail fast if required external tools are missing."""
    required = [("git", "Git: https://git-scm.com/downloads")]
    if ctx.kind != "pitch":
        required.append(
            ("ssh-keygen", "OpenSSH (sollte mit dem System geliefert werden)")
        )
    if ctx.mode == "github":
        required.append(("gh", "GitHub CLI: https://cli.github.com (brew install gh)"))

    missing = [(name, hint) for name, hint in required if shutil.which(name) is None]
    if missing:
        lines = [f"  • {name} — {hint}" for name, hint in missing]
        raise click.ClickException("Fehlende Tools:\n" + "\n".join(lines))

    if ctx.mode == "github":
        result = subprocess.run(
            ["gh", "auth", "status"], capture_output=True, text=True
        )
        if result.returncode != 0:
            raise click.ClickException(
                "GitHub CLI ist nicht eingeloggt. Bitte `gh auth login` ausführen."
            )


def run_wizard(ctx: BootstrapContext) -> None:
    """Run the full bootstrap wizard pipeline."""
    check_prerequisites(ctx)

    steps = steps_for(ctx)
    total = len(steps)
    completed = 0

    for idx, step_cls in enumerate(steps, 1):
        step = step_cls()
        ui.step_header(idx, step.name, completed, total)

        try:
            if step.check(ctx):
                completed += 1
                continue
            step.run(ctx)
            completed += 1
            ui.success(f"Step {idx} abgeschlossen")
        except click.ClickException:
            raise
        except Exception as exc:
            ui.error(f"Fehler in Step {idx}: {exc}")
            raise click.ClickException(str(exc))

    # All steps done — show summary
    github_url = ctx.github_url if ctx.mode == "github" else None
    keychain_service = None
    if ctx.kind != "pitch":
        from cli.ansible_commands import keychain_service_name

        keychain_service = keychain_service_name(ctx.project_name)
    byos_deploy_key_command = None
    if ctx.kind == "fullstack" and ctx.provider == "byos":
        from .steps.project import (
            BYOS_DEPLOY_PUBLIC_KEY_FILE,
            byos_deploy_key_install_command,
        )

        deploy_key_path = ctx.deployment_dir / BYOS_DEPLOY_PUBLIC_KEY_FILE
        byos_deploy_key_command = byos_deploy_key_install_command(
            ctx, deploy_key_path.read_text().strip()
        )
    ui.summary_box(
        project_name=ctx.project_name,
        project_dir=str(ctx.project_dir),
        github_url=github_url,
        domain=ctx.base_domain,
        kind=ctx.kind,
        keychain_service=keychain_service,
        provider=ctx.provider,
        byos_deploy_key_command=byos_deploy_key_command,
    )
