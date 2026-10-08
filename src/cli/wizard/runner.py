"""Pipeline runner: pick step list based on mode, check prereqs, run, summarize."""

from __future__ import annotations

import shutil
import subprocess

import click

from cli import wizard_output as ui

from .base import WizardStep
from .context import BootstrapContext
from .steps.auth0 import Auth0Step
from .steps.byos import ByosStep
from .steps.cloudflare import CloudflareStep
from .steps.domain import DomainStep
from .steps.finalize import FinalizeStep
from .steps.hetzner import HetznerStep
from .steps.pitch_finalize import PitchFinalizeStep
from .steps.pitch_project import PitchProjectStep
from .steps.project import ProjectStep
from .steps.shared_deployment import SharedDeploymentStep

FULLSTACK_STEPS: list[type[WizardStep]] = [
    SharedDeploymentStep,
    DomainStep,
    HetznerStep,
    ProjectStep,
    FinalizeStep,
]
# Bring-your-own-server: no Hetzner token and no domain-registrar step — the user
# brings an existing VPS and points DNS at it themselves (explained in ByosStep).
BYOS_STEPS: list[type[WizardStep]] = [
    SharedDeploymentStep,
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
    steps = list(BYOS_STEPS if ctx.provider == "byos" else FULLSTACK_STEPS)
    if ctx.auth0_tenant:
        steps.insert(-1, Auth0Step)
    return steps


def check_prerequisites(ctx: BootstrapContext) -> None:
    """Fail fast if required external tools are missing."""
    if ctx.auth0_tenant and ctx.without_auth:
        raise click.ClickException("Use either --auth0-tenant or --without-auth.")
    if ctx.kind != "pitch" and not ctx.auth0_tenant and not ctx.without_auth:
        from cli.template_commands import template_authentication

        if (
            template_authentication(ctx.template_source, ctx.template_version)
            == "auth0"
        ):
            if ctx.non_interactive:
                raise click.ClickException(
                    "This template supports Auth0: pass --auth0-tenant <tenant> "
                    "or --without-auth before bootstrap."
                )
            if click.confirm("Set up Auth0 login for this template?", default=True):
                ctx.auth0_tenant = click.prompt(
                    "Auth0 tenant (for example, example.eu.auth0.com)"
                )
            else:
                ctx.without_auth = True
    if ctx.auth0_tenant:
        if ctx.kind == "pitch":
            raise click.ClickException("--auth0-tenant requires --kind fullstack.")
        from cli.auth0_commands import check_auth0_login

        check_auth0_login(ctx.auth0_tenant)
    if ctx.kind != "pitch":
        from cli.preflight import check_local_prerequisites

        check_local_prerequisites(
            browser=ctx.provider == "hetzner" and not ctx.hetzner_token
        )
    required = [("git", "Git: https://git-scm.com/downloads")]
    if ctx.kind != "pitch":
        required.append(("ssh-keygen", "OpenSSH (included with macOS)"))
    if ctx.mode == "github":
        required.append(("gh", "GitHub CLI: https://cli.github.com (brew install gh)"))

    missing = [(name, hint) for name, hint in required if shutil.which(name) is None]
    if missing:
        lines = [f"  • {name} — {hint}" for name, hint in missing]
        raise click.ClickException("Missing tools:\n" + "\n".join(lines))

    if ctx.mode == "github":
        result = subprocess.run(
            ["gh", "auth", "status"], capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            raise click.ClickException(
                "GitHub CLI is not signed in. Run `gh auth login`."
            )

        # ghcr.io image pulls need `read:packages`. This used to be noticed deep
        # inside the project step, several minutes and a Hetzner project later;
        # settle it here, before anything has been created.
        from cli.bootstrap import _ensure_ghcr_scopes, _gh_token_has_scope

        if not (
            _gh_token_has_scope("read:packages")
            or _gh_token_has_scope("write:packages")
        ):
            if ctx.non_interactive:
                raise click.ClickException(
                    "The gh token is missing the 'read:packages' scope needed for "
                    "ghcr.io pulls. Run `gh auth refresh -h github.com -s "
                    "read:packages` once — it opens a browser, so it cannot be "
                    "done as part of an unattended run."
                )
            _ensure_ghcr_scopes()


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
            ui.success(f"Step {idx} complete")
        except click.ClickException:
            raise
        # Top-level boundary: any failure here is reported to the user and
        # handled, never surfaced as a traceback.
        except Exception as exc:
            ui.error(f"Error in step {idx}: {exc}")
            raise click.ClickException(str(exc)) from exc

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
        cluster_owner=ctx.cluster_config["owner"] if ctx.cluster_config else None,
    )
    if ctx.auth0_tenant:
        ui.info(
            "After deployment, verify the application login:\n"
            f"  startup auth0 validate --tenant {ctx.auth0_tenant} "
            f"--base-domain {ctx.base_domain}"
        )
