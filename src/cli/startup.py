#!/usr/bin/env python3
"""
Startup CLI - Command line tool for Deploy Your Startup operations
"""

import sys
from pathlib import Path

import click

from cli.auth0_commands import auth0
from cli.cluster_commands import cluster
from cli.commands.ansible import ansible
from cli.commands.deploy import deploy
from cli.commands.hetzner import hetzner
from cli.commands.lint_contract import lint_contract
from cli.commands.secrets import _password_scope, secrets
from cli.commands.tunnel import tunnel
from cli.evidence_commands import evidence
from cli.factory_commands import factory
from cli.preflight import doctor
from cli.sentry_commands import sentry
from cli.skill_commands import skills
from cli.template_commands import template
from cli.user_config_commands import config
from cli.verification import verify

__all__ = ["_password_scope", "bootstrap", "cli"]  # imported by tests and factory


@click.group()
def cli():
    """Startup CLI - Command line tool for Deploy Your Startup operations.

    Browser setup: STARTUP_BROWSER=auto (default), chrome (Apple Passwords),
    or chromium. Auto prefers Chrome, otherwise downloads matching Chromium.
    """


# Command groups live in their own modules; `bootstrap` and `sync` follow below.
for command in (
    ansible,
    auth0,
    cluster,
    config,
    deploy,
    doctor,
    evidence,
    factory,
    hetzner,
    lint_contract,
    secrets,
    sentry,
    skills,
    template,
    tunnel,
    verify,
):
    cli.add_command(command)


# === BOOTSTRAP COMMAND ===
@cli.command("bootstrap")
@click.option(
    "--verify/--no-verify",
    "verify_deployment",
    default=True,
    help="Verify the Hetzner full-stack deployment after setup.",
)
@click.option(
    "--verify-timeout",
    type=click.IntRange(min=0),
    default=900,
    show_default=True,
    help="Seconds to wait for deployment and DNS verification.",
)
@click.option(
    "--max-monthly-cost",
    help="Maximum gross Hetzner monthly estimate in the project currency; abort if exceeded or unavailable.",
)
@click.option("--verbose", "-V", is_flag=True, help="Verbose output")
@click.option(
    "--kind",
    type=click.Choice(["fullstack", "pitch"]),
    help="Project kind (skips the interactive stack selection).",
)
@click.option(
    "--provider",
    type=click.Choice(["hetzner", "byos"]),
    help="Where to deploy (full-stack only).",
)
@click.option(
    "--site-policy",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Reviewed public operator/privacy JSON; no account required.",
)
@click.option("--project-name", help="Project name in kebab-case.")
@click.option("--base-domain", help="Domain the project is served under.")
@click.option("--additional-domains", help="Comma-separated extra domains.")
@click.option("--sentry-dsn", help="Existing Sentry DSN instead of creating a project.")
@click.option("--github-username", help="GitHub user or org (default: gh user).")
@click.option("--output-dir", help="Directory the project is created in.")
@click.option(
    "--template",
    "template_source",
    help="Copier template Git URL or local repository "
    "(default: the Django template, or the pitch template with --kind pitch).",
)
@click.option(
    "--template-version",
    help="Template tag, branch or commit "
    "(default: the tested release of built-in templates, HEAD for others).",
)
@click.option(
    "--domain-owned/--buy-domain",
    default=None,
    help="Whether the domain already belongs to you (skips that question).",
)
@click.option(
    "--domain-contact",
    help="E-mail of the KonsoleH contact that owns a domain bought with --buy-domain.",
)
@click.option(
    "--confirm-domain-order",
    is_flag=True,
    help="With --buy-domain: place Hetzner's binding domain order without asking.",
)
@click.option(
    "--auth0-tenant",
    help="Create a dedicated Auth0 app/API for a template with oauth2-proxy.",
)
@click.option(
    "--without-auth", is_flag=True, help="Explicitly skip optional template login."
)
@click.option(
    "--sentry-org",
    help="Create a Sentry project in this organization (needs a personal Sentry token).",
)
@click.option("--sentry-team", help="Sentry team for the new project.")
@click.option(
    "--without-sentry", is_flag=True, help="Explicitly skip optional error tracking."
)
@click.option("--hetzner-token", help="Use this token instead of creating one.")
@click.option(
    "--hetzner-token-stdin",
    is_flag=True,
    help="Read a Hetzner token from stdin instead of a process argument.",
)
@click.option(
    "--shared-cluster",
    is_flag=True,
    help="Own a reusable cluster; use a project namespace.",
)
@click.option(
    "--cluster",
    "cluster_path",
    type=click.Path(exists=True, path_type=Path),
    help="Attach to a public descriptor exported by startup cluster export.",
)
@click.option(
    "--deployment-ref",
    default="main",
    show_default=True,
    help="Reviewed shared-workflow ref used by the generated project.",
)
@click.option(
    "--ssh-public-key",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Your public SSH key for server access (not a GitHub token).",
)
@click.option("--byos-host", help="Existing server's IP or hostname (provider byos).")
@click.option("--byos-ssh-user", help="SSH user on that server (default: root).")
@click.option("--cloudflare-token", help="Cloudflare API token (pitch only).")
@click.option(
    "--cloudflare-account-id",
    help="Cloudflare account to use when several exist (pitch only).",
)
@click.option(
    "--yes",
    "-y",
    "assume_yes",
    is_flag=True,
    help="Run without questions — every answer must come from the options above.",
)
def bootstrap(**options):
    """Bootstrap a new startup — guided wizard.

    Choose a project name, stack, folder and deployment target, then confirm
    the GitHub account. Configure the required accounts and create your
    repository, secrets and first push.

    Each step checks if its outcome already exists, so you can safely
    re-run after an interruption.

    Every question can also be answered up front with an option; with --yes
    the wizard then runs unattended, which is what automation should use
    instead of driving the prompts with a tool like `expect`.
    """
    from cli.bootstrap_command import BootstrapOptions, run_bootstrap

    run_bootstrap(BootstrapOptions(**options))


@cli.group(invoke_without_command=True)
@click.option(
    "--owner", default=None, help="Target GitHub owner (defaults to gh auth user)"
)
@click.option(
    "--repo-name",
    default="deploy-your-startup",
    show_default=True,
    help="Target shared deploy repository name",
)
@click.option(
    "--source-owner",
    default="Deploy-your-Startup",
    show_default=True,
    help="Shared deploy template owner",
)
@click.option(
    "--source-repo",
    default="deploy-template",
    show_default=True,
    help="Shared deploy template repository",
)
@click.option(
    "--source-version",
    default="main",
    show_default=True,
    help="Source template branch, tag or full commit SHA to sync",
)
@click.option("--private/--public", default=True, show_default=True)
@click.option("--dry-run", is_flag=True, help="Preview sync without commit/push")
@click.pass_context
def sync(
    ctx, owner, repo_name, source_owner, source_repo, source_version, private, dry_run
):
    """Sync the shared deploy template into your GitHub account."""
    if ctx.invoked_subcommand is None:
        from cli.sync_commands import sync_deploy_repo

        sync_deploy_repo(
            owner=owner,
            repo_name=repo_name,
            source_owner=source_owner,
            source_repo=source_repo,
            source_version=source_version,
            private=private,
            dry_run=dry_run,
        )


def main():
    """Main entry point for the CLI"""
    return cli(prog_name="startup")


if __name__ == "__main__":
    sys.exit(cli())
