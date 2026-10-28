"""``startup bootstrap``: collect the answers, confirm them and run the wizard.

Every answer can come from an option. Interactive runs prompt for the rest;
with ``--yes`` a missing answer is an error instead of a prompt. The steps that
create accounts, repositories and servers live in ``cli.wizard``.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import click

from cli import bootstrap_wizard
from cli import wizard_output as ui
from cli.template_commands import (
    DEFAULT_TEMPLATE,
    PITCH_TEMPLATE,
    VUE_TEMPLATE,
    default_template_version,
    template_supports_shared_cluster,
)

POSTGRES_VERSION = "18.6"
PROJECT_NAME = re.compile(r"[a-z](?:[a-z0-9-]{0,61}[a-z0-9])?")
DOMAIN_NAME = re.compile(
    r"(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}"
)


@dataclass
class BootstrapOptions:
    """The ``startup bootstrap`` options; the questions fill in what is missing."""

    site_policy: Path | None
    max_monthly_cost: str | None
    verify_deployment: bool
    verify_timeout: int
    verbose: bool
    kind: str | None
    provider: str | None
    project_name: str | None
    base_domain: str | None
    additional_domains: str | None
    sentry_dsn: str | None
    github_username: str | None
    output_dir: str | None
    template_source: str | None
    template_version: str | None
    auth0_tenant: str | None
    without_auth: bool
    domain_owned: bool | None
    domain_contact: str | None
    confirm_domain_order: bool
    hetzner_token: str | None
    hetzner_token_stdin: bool
    shared_cluster: bool
    cluster_path: Path | None
    deployment_ref: str
    ssh_public_key: Path | None
    byos_host: str | None
    byos_ssh_user: str | None
    cloudflare_token: str | None
    cloudflare_account_id: str | None
    assume_yes: bool

    def require(self, value, option: str):
        """In --yes mode a missing answer is an error, not a prompt."""
        if self.assume_yes and not value:
            raise click.ClickException(f"--yes needs {option} to be set as well.")
        return value

    @property
    def project_dir(self) -> Path:
        return Path(_answered(self.output_dir)).expanduser().resolve() / _answered(
            self.project_name
        )


def _answered(value: str | None) -> str:
    """A value the questions above always fill in; None here is a bug."""
    if value is None:
        raise RuntimeError("bootstrap reached the wizard with an unanswered question")
    return value


def _read_hetzner_token_from_stdin(options: BootstrapOptions) -> None:
    if not options.hetzner_token_stdin:
        return
    if options.hetzner_token:
        raise click.ClickException(
            "Use either --hetzner-token or --hetzner-token-stdin, not both."
        )
    options.hetzner_token = sys.stdin.read().strip()
    if not options.hetzner_token:
        raise click.ClickException("No Hetzner token was provided on stdin.")


def _validate_deployment_ref(deployment_ref: str) -> None:
    if (
        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", deployment_ref)
        or ".." in deployment_ref
    ):
        raise click.ClickException(
            "--deployment-ref must be a Git branch, tag or commit without whitespace."
        )


def _attach_to_cluster(options: BootstrapOptions) -> dict | None:
    """Read ``--cluster``; an attached startup deploys to the owner's master."""
    if not options.cluster_path:
        return None
    from cli.cluster_commands import read_descriptor

    if (
        options.shared_cluster
        or options.provider not in (None, "byos")
        or options.byos_host
        or (options.hetzner_token and options.domain_owned is not False)
    ):
        raise click.ClickException(
            "--cluster cannot be combined with --shared-cluster, Hetzner provisioning, "
            "--byos-host or a Hetzner token (except for the DNS of --buy-domain)."
        )
    cluster_config = read_descriptor(options.cluster_path)
    options.provider = "byos"
    master = next(node for node in cluster_config["nodes"] if node["role"] == "master")
    options.byos_host, options.byos_ssh_user = master["host"], master["ssh_user"]
    return cluster_config


def _ask_project_name(options: BootstrapOptions) -> None:
    # Validated even when passed as an option: it becomes a repository name,
    # a Kubernetes namespace and a Keychain entry.
    while not options.project_name or not PROJECT_NAME.fullmatch(options.project_name):
        if options.project_name:
            ui.error(
                "Use lowercase letters, numbers and hyphens (for example, my-startup)."
            )
            if options.assume_yes:
                raise click.ClickException(
                    f"--project-name '{options.project_name}' must be kebab-case, end in an "
                    "alphanumeric character, and contain at most 63 characters."
                )
        options.require(options.project_name, "--project-name")
        options.project_name = ui.text_input("Project name (for example, my-startup)")


def _ask_stack(options: BootstrapOptions, cluster_config: dict | None) -> None:
    if options.kind is None:
        options.require(options.kind, "--kind")
        if options.template_source:
            options.kind = (
                "pitch" if options.template_source == PITCH_TEMPLATE else "fullstack"
            )
        else:
            choices = [
                "Django/FastAPI — backend, Postgres and HTTPS",
                "Vue + Django/FastAPI — frontend, backend, Postgres and HTTPS",
            ]
            # A landing page has no servers, so it cannot use a cluster.
            if not (options.provider or options.shared_cluster or cluster_config):
                choices.append("Astro — static landing page on Cloudflare Pages")
            choice = ui.numbered_choice(
                "Which stack would you like to start with?", choices
            )
            options.kind, options.template_source = [
                ("fullstack", DEFAULT_TEMPLATE),
                ("fullstack", VUE_TEMPLATE),
                ("pitch", PITCH_TEMPLATE),
            ][choice - 1]

    if options.template_source is None:
        options.template_source = (
            PITCH_TEMPLATE if options.kind == "pitch" else DEFAULT_TEMPLATE
        )


def _ask_output_dir(options: BootstrapOptions) -> None:
    from cli.factory_commands import factory_in_directory

    workspace = factory_in_directory(Path.cwd())
    if not options.output_dir and workspace:
        options.output_dir = str(workspace / "projects/startups")
    if not options.output_dir:
        from cli.factory_paths import bootstrap_folder

        default = str(bootstrap_folder(assume_yes=options.assume_yes))
        options.output_dir = (
            default
            if options.assume_yes
            else ui.text_input(
                "Project folder",
                default=default,
                type=click.Path(file_okay=False),
            )
        )
    ui.info(f"Project path: {options.project_dir}")


def _ask_provider(options: BootstrapOptions) -> None:
    if options.kind != "fullstack":
        options.provider = "hetzner"
    elif options.provider is None:
        if options.assume_yes:
            options.provider = "hetzner"
        else:
            provider_choice = ui.numbered_choice(
                "Where should it run?",
                [
                    "Your existing server — you manage DNS and provide SSH access",
                    "Hetzner Cloud — provision servers and guide domain setup",
                ],
            )
            options.provider = "byos" if provider_choice == 1 else "hetzner"


def _ask_domain(options: BootstrapOptions, cluster_config: dict | None) -> None:
    if (
        options.kind == "fullstack"
        and options.provider == "byos"
        and not cluster_config
    ):
        if options.domain_owned is False:
            raise click.ClickException(
                "BYOS uses your existing domain. Register it with your chosen "
                "registrar first, then use --domain-owned."
            )
        options.domain_owned = True
        ui.info(
            "Use a domain you own and point its DNS at your server; no Hetzner account is needed."
        )

    # Ask whether one exists first, so "no domain yet" leads to registering
    # one instead of a prompt that assumes ownership.
    if (
        not options.base_domain
        and options.kind == "fullstack"
        and options.domain_owned is None
    ):
        options.require(options.base_domain, "--base-domain")
        options.domain_owned = (
            ui.numbered_choice(
                "Do you already have a domain?",
                ["Yes, I own a domain", "No, register a new one through Hetzner"],
            )
            == 1
        )
    domain_label = {
        True: "Your domain",
        False: "Domain to register",
        None: "Domain",
    }[options.domain_owned]
    while not options.base_domain or not DOMAIN_NAME.fullmatch(options.base_domain):
        if options.base_domain:
            ui.error("Enter a domain like example.com (lowercase, without https://).")
            if options.assume_yes:
                raise click.ClickException(
                    f"--base-domain '{options.base_domain}' is not a valid domain name."
                )
        options.require(options.base_domain, "--base-domain")
        options.base_domain = (
            ui.text_input(f"{domain_label} (for example, example.com)").strip().lower()
        )


def _ask_optional_settings(options: BootstrapOptions) -> None:
    # Kept out of the first-deploy path unless the user asks for them.
    options.additional_domains = options.additional_domains or ""
    options.sentry_dsn = options.sentry_dsn or os.environ.get("SENTRY_DSN", "")
    if (
        options.kind != "fullstack"
        or options.assume_yes
        or not ui.confirm("Configure extra domains or error tracking?", default=False)
    ):
        return
    if not options.additional_domains:
        options.additional_domains = ui.text_input(
            "Extra domains (comma-separated, Enter to skip)",
            default="",
            show_default=False,
        )
    if not options.sentry_dsn:
        options.sentry_dsn = ui.text_input(
            "Sentry DSN (Enter to skip)", default="", show_default=False
        )


def _ask_github_owner(options: BootstrapOptions) -> None:
    # An explicit owner may be an organization; preserve it without changing
    # the authenticated user. Unattended callers must never receive a prompt.
    if options.github_username:
        ui.info(f"GitHub repository owner: {options.github_username}")
        return
    if options.assume_yes:
        from cli.sync_commands import _github_owner

        options.github_username = _github_owner(None)
        return
    from cli.github_setup import confirm_github_owner

    try:
        options.github_username = confirm_github_owner(_answered(options.project_name))
    except FileNotFoundError as exc:
        raise click.ClickException(
            "GitHub CLI is missing. Install gh, then rerun startup bootstrap. "
            "No project resources were created."
        ) from exc


def _template_name(template_source: str) -> str:
    if template_source == DEFAULT_TEMPLATE:
        return "Django/FastAPI"
    if template_source == VUE_TEMPLATE:
        return "Vue + Django/FastAPI"
    return template_source.rstrip("/").removesuffix(".git").rsplit("/", 1)[-1]


def _summary(options: BootstrapOptions, cluster_config: dict | None) -> dict:
    summary = {
        "Type": "Full-Stack"
        if options.kind == "fullstack"
        else "Pitch (Cloudflare Pages)",
        "Project": options.project_name,
        "Folder": str(options.project_dir),
        "Domain": _answered(options.base_domain)
        + (" (register through Hetzner)" if options.domain_owned is False else ""),
        "GitHub": f"{options.github_username}/{options.project_name}",
    }
    if options.kind != "fullstack":
        summary["Template"] = f"Pitch {options.template_version}"
        return summary
    summary["Provider"] = (
        "Hetzner" if options.provider == "hetzner" else "Bring your own server"
    )
    if cluster_config:
        summary["Cluster owner"] = cluster_config["owner"]
        summary["Infrastructure"] = "Existing cluster; application deployment only"
    elif options.shared_cluster:
        summary["Cluster"] = "Owned here; additional startups may attach"
    summary["Registry"] = f"ghcr.io/{options.github_username}"
    summary["Postgres"] = POSTGRES_VERSION
    summary["Template"] = (
        f"{_template_name(_answered(options.template_source))} {options.template_version}"
    )
    return summary


def _confirm_or_cancel(options: BootstrapOptions) -> None:
    if not options.assume_yes:
        ui.info("Code and infrastructure stay in your accounts.")
        ui.warning("Creating cloud resources incurs provider charges.")
        if not ui.confirm("Create this project and start setup?", default=False):
            ui.info("Cancelled. No resources were created.")
            raise SystemExit(0)
    if options.assume_yes and options.provider == "byos" and not options.byos_host:
        raise click.ClickException("--provider byos needs --byos-host as well.")


def _wizard_context(
    options: BootstrapOptions, cluster_config: dict | None, policy_data, max_cost
):
    return bootstrap_wizard.BootstrapContext(
        site_policy=policy_data,
        project_name=_answered(options.project_name),
        base_domain=_answered(options.base_domain),
        additional_domains=_answered(options.additional_domains),
        github_username=_answered(options.github_username),
        postgres_version=POSTGRES_VERSION,
        sentry_dsn=_answered(options.sentry_dsn),
        # Absolute: steps run git with cwd=output_dir and pass project_dir as
        # the destination, so a relative path would be resolved twice and the
        # clone would land in <output_dir>/<output_dir>/<name>.
        output_dir=Path(_answered(options.output_dir)).expanduser().resolve(),
        template_source=_answered(options.template_source),
        template_version=_answered(options.template_version),
        auth0_tenant=options.auth0_tenant,
        without_auth=options.without_auth,
        kind=_answered(options.kind),
        provider=_answered(options.provider),
        deployment_ref=options.deployment_ref,
        shared_cluster=options.shared_cluster,
        cluster_config=cluster_config,
        ssh_public_key_path=options.ssh_public_key,
        byos_host=options.byos_host,
        byos_ssh_user=options.byos_ssh_user or "root",
        hetzner_token=options.hetzner_token,
        cloudflare_api_token=options.cloudflare_token,
        cloudflare_account_id=options.cloudflare_account_id,
        max_monthly_cost=max_cost,
        non_interactive=options.assume_yes,
        domain_owned=options.domain_owned,
        domain_contact=options.domain_contact,
        confirm_domain_order=options.confirm_domain_order,
    )


def _check_domain_is_free(base_domain: str) -> None:
    # The attach flow creates the project before ordering, so a taken domain
    # must stop it here rather than after the repository exists.
    from cli.hetzner.domain_check import check_domain

    try:
        availability = check_domain(base_domain, include_price=False).availability
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if availability == "registered":
        raise click.ClickException(
            f"{base_domain} is already registered; choose another domain or use --domain-owned."
        )


def _run_wizard_and_register(ctx) -> None:
    from cli.factory_commands import register_project

    try:
        bootstrap_wizard.run_wizard(ctx)
    finally:
        # Keep a created repository discoverable even if setup needs to resume.
        bootstrap_failed = sys.exc_info()[0] is not None
        try:
            register_project(ctx.project_dir)
        except (click.ClickException, OSError, ValueError) as exc:
            if bootstrap_failed:
                ui.warning(f"Factory registration needs attention: {exc}")
            else:
                raise


def run_bootstrap(options: BootstrapOptions) -> None:
    from cli.factory_commands import check_registration
    from cli.site_policy import read_policy
    from cli.wizard.steps.cost import monthly_cost

    policy_data = read_policy(options.site_policy) if options.site_policy else None
    max_cost = (
        monthly_cost(options.max_monthly_cost)
        if options.max_monthly_cost is not None
        else None
    )
    _read_hetzner_token_from_stdin(options)
    ui.banner(animate=not options.assume_yes)

    _validate_deployment_ref(options.deployment_ref)
    cluster_config = _attach_to_cluster(options)
    if (options.shared_cluster or cluster_config) and options.kind == "pitch":
        raise click.ClickException("Cluster options require --kind fullstack.")

    _ask_project_name(options)
    _ask_stack(options, cluster_config)
    _ask_output_dir(options)
    _ask_provider(options)
    if (options.shared_cluster or cluster_config) and options.kind != "fullstack":
        raise click.ClickException("Cluster options require --kind fullstack.")
    _ask_domain(options, cluster_config)
    _ask_optional_settings(options)
    _ask_github_owner(options)
    check_registration(
        Path(_answered(options.output_dir)).expanduser().resolve(),
        _answered(options.project_name),
        _answered(options.github_username),
    )

    options.template_version = options.template_version or default_template_version(
        _answered(options.template_source)
    )
    if (
        options.shared_cluster or cluster_config
    ) and not template_supports_shared_cluster(
        _answered(options.template_source), _answered(options.template_version)
    ):
        raise click.ClickException(
            "This template revision does not support shared clusters. Use --template-version "
            "with a compatible release or reviewed ref; no account or server setup was started."
        )
    ui.input_summary(_summary(options, cluster_config))
    _confirm_or_cancel(options)

    ctx = _wizard_context(options, cluster_config, policy_data, max_cost)
    if cluster_config and options.domain_owned is False:
        _check_domain_is_free(_answered(options.base_domain))
    _run_wizard_and_register(ctx)

    if (
        options.verify_deployment
        and ctx.kind == "fullstack"
        and ctx.provider == "hetzner"
    ):
        from cli.verification import verify_project

        verify_project(ctx.project_dir, timeout=options.verify_timeout)
