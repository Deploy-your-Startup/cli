"""Pipeline runner: pick step list based on mode, check prereqs, run, summarize."""

from __future__ import annotations

import shutil
import subprocess

import click

from cli import wizard_output as ui

from .base import WizardStep
from .context import BootstrapContext
from .steps.attached_dns import AttachedDnsStep
from .steps.auth0 import Auth0Step
from .steps.byos import ByosStep
from .steps.cloudflare import CloudflareStep
from .steps.cost import CostStep
from .steps.domain import DomainStep
from .steps.finalize import FinalizeStep
from .steps.hetzner import HetznerStep
from .steps.pitch_finalize import PitchFinalizeStep
from .steps.pitch_project import PitchProjectStep
from .steps.project import ProjectStep
from .steps.sentry import SentryStep
from .steps.shared_deployment import SharedDeploymentStep

FULLSTACK_STEPS: list[type[WizardStep]] = [
    SharedDeploymentStep,
    DomainStep,
    HetznerStep,
    ProjectStep,
    CostStep,
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
# The generated deploy.yml calls deploy-pages.yml from the owner's shared
# deployment repository, so a pitch needs it as much as a full-stack project.
PITCH_STEPS: list[type[WizardStep]] = [
    SharedDeploymentStep,
    CloudflareStep,
    DomainStep,
    PitchProjectStep,
    PitchFinalizeStep,
]


def steps_for(ctx: BootstrapContext) -> list[type[WizardStep]]:
    if ctx.kind == "pitch":
        return PITCH_STEPS
    steps = list(BYOS_STEPS if ctx.provider == "byos" else FULLSTACK_STEPS)
    if ctx.cluster_config:
        steps = [step for step in steps if step is not CostStep]
        if ctx.domain_owned is False:
            # Attaching creates no servers. A bought domain gets its own Hetzner
            # project (DNS only) and its zone before the order, so the registry
            # finds the nameservers serving it.
            steps.insert(steps.index(ByosStep), HetznerStep)
            finalize = steps.index(FinalizeStep)
            steps[finalize:finalize] = [AttachedDnsStep, DomainStep]
    if ctx.auth0_tenant:
        steps.insert(-1, Auth0Step)
    if ctx.sentry_org:
        steps.insert(-1, SentryStep)
    return steps


def _choose_authentication(ctx: BootstrapContext) -> None:
    """Settle optional template login and check its CLI login up front."""
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


def _choose_error_tracking(ctx: BootstrapContext) -> None:
    """Settle optional Sentry and check its login up front.

    Unattended runs without a choice skip it: unlike login, error tracking
    never changes what the template needs to work.
    """
    if sum(map(bool, (ctx.sentry_org, ctx.sentry_dsn, ctx.without_sentry))) > 1:
        raise click.ClickException(
            "Use only one of --sentry-org, --sentry-dsn or --without-sentry."
        )
    if ctx.kind == "pitch":
        if ctx.sentry_org:
            raise click.ClickException("--sentry-org requires --kind fullstack.")
        return
    if not (ctx.sentry_org or ctx.sentry_dsn or ctx.without_sentry) and (
        not ctx.non_interactive
    ):
        if click.confirm("Set up Sentry error tracking?", default=False):
            from cli.sentry import default_org
            from cli.sentry_login import ensure_sentry_login

            # The login comes first: it may leave the default organization.
            ensure_sentry_login()
            ctx.sentry_org = click.prompt(
                "Sentry organization slug", default=default_org()
            )
        else:
            ctx.without_sentry = True
    if ctx.sentry_org:
        from cli.sentry import check_sentry_login

        check_sentry_login(ctx.sentry_org)


def _check_github_login(ctx: BootstrapContext) -> None:
    result = subprocess.run(
        ["gh", "auth", "status"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise click.ClickException("GitHub CLI is not signed in. Run `gh auth login`.")

    # ghcr.io image pulls need `read:packages`. This used to be noticed deep
    # inside the project step, several minutes and a Hetzner project later;
    # settle it here, before anything has been created.
    from cli.bootstrap import _ensure_ghcr_scopes, _gh_token_has_scope

    if not (
        _gh_token_has_scope("read:packages") or _gh_token_has_scope("write:packages")
    ):
        if ctx.non_interactive:
            raise click.ClickException(
                "The gh token is missing the 'read:packages' scope needed for "
                "ghcr.io pulls. Run `gh auth refresh -h github.com -s "
                "read:packages` once — it opens a browser, so it cannot be "
                "done as part of an unattended run."
            )
        _ensure_ghcr_scopes()


def check_prerequisites(ctx: BootstrapContext) -> None:
    """Fail fast if required external tools are missing."""
    from cli.template_commands import require_committed_template

    require_committed_template(ctx.template_source, ctx.template_version)
    if ctx.site_policy is not None:
        from cli.site_policy import check_template_policy

        check_template_policy(
            ctx.template_source, ctx.template_version, ctx.site_policy
        )
    _choose_authentication(ctx)
    _choose_error_tracking(ctx)
    if ctx.kind != "pitch":
        from cli.preflight import check_local_prerequisites

        check_local_prerequisites(
            browser=ctx.provider == "hetzner" and not ctx.hetzner_token,
            ssh_key=False,
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
        _check_github_login(ctx)

    # Resolve personal SSH access before creating provider resources. A resumed
    # project can use its saved configuration and does not need the key again.
    if ctx.kind != "pitch" and (
        ctx.ssh_public_key_path is not None
        or not (ctx.project_dir / ".copier-answers.yml").is_file()
    ):
        from .base import prompt_user_public_key

        ctx.user_public_key = prompt_user_public_key(
            non_interactive=ctx.non_interactive, public_key_path=ctx.ssh_public_key_path
        )


def run_wizard(ctx: BootstrapContext) -> None:
    """Run the full bootstrap wizard pipeline."""
    check_prerequisites(ctx)

    steps = steps_for(ctx)
    total = len(steps)

    for idx, step_cls in enumerate(steps, 1):
        step = step_cls()
        ui.step_header(idx, step.name, total)

        try:
            if step.check(ctx):
                continue
            step.run(ctx)
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
    vault_password_location = None
    if ctx.kind != "pitch":
        from cli.wizard.vault_guard import vault_password_label

        vault_password_location = vault_password_label(ctx.project_name)
    ui.summary_box(
        project_name=ctx.project_name,
        project_dir=str(ctx.project_dir),
        github_url=github_url,
        domain=ctx.base_domain,
        kind=ctx.kind,
        vault_password_location=vault_password_location,
        provider=ctx.provider,
        cluster_owner=ctx.cluster_config["owner"] if ctx.cluster_config else None,
    )
    if ctx.auth0_tenant:
        ui.info(
            "After deployment, verify the application login:\n"
            f"  startup auth0 validate --tenant {ctx.auth0_tenant} "
            f"--base-domain {ctx.base_domain}"
        )
