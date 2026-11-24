"""Choose and validate the project's optional login before provisioning."""

import sys

import click

from cli import wizard_output as ui
from cli.auth0_commands import check_auth0_login
from cli.oidc import discover
from cli.wizard.context import BootstrapContext


def _prompt_for_oidc(ctx: BootstrapContext, provider: str) -> None:
    ui.info(
        f"Register a confidential {provider} client with the callback "
        f"https://{ctx.base_domain}/oauth2/callback."
    )
    ctx.oidc_issuer = click.prompt(f"{provider} issuer URL")


def _choose_provider(ctx: BootstrapContext, capability: str) -> None:
    if capability == "auth0":
        if ctx.non_interactive:
            raise click.ClickException(
                "This template supports Auth0: pass --auth0-tenant <tenant> "
                "or --without-auth before bootstrap."
            )
        choice = 2 if click.confirm("Set up Auth0 login for this template?") else 1
    else:
        if ctx.non_interactive:
            raise click.ClickException(
                "Choose --auth0-tenant, --oidc-issuer or --without-auth."
            )
        choice = ui.numbered_choice(
            "Application login",
            ["No login", "Auth0", "Keycloak (existing realm)", "Other OIDC provider"],
        )
    if choice == 1:
        ctx.without_auth = True
    elif choice == 2:
        ctx.auth0_tenant = click.prompt(
            "Auth0 tenant (for example, example.eu.auth0.com)"
        )
    else:
        _prompt_for_oidc(ctx, "Keycloak realm" if choice == 3 else "OIDC")


def _prepare_oidc(ctx: BootstrapContext) -> None:
    assert ctx.oidc_issuer is not None
    if ctx.non_interactive and not (
        ctx.oidc_client_id and ctx.oidc_client_secret_stdin
    ):
        raise click.ClickException(
            "--yes with --oidc-issuer needs --oidc-client-id and "
            "--oidc-client-secret-stdin."
        )
    discover(ctx.oidc_issuer)
    ctx.oidc_client_id = ctx.oidc_client_id or click.prompt("OIDC client ID")
    if ctx.oidc_client_secret_stdin:
        secret = sys.stdin.readline().rstrip("\r\n")
    else:
        secret = click.prompt("OIDC client secret", hide_input=True)
    if not secret:
        raise click.ClickException("The OIDC client secret is empty.")
    ctx.oidc_client_secret = secret


def choose_authentication(ctx: BootstrapContext) -> None:
    from cli.template_commands import template_authentication

    choices = (ctx.auth0_tenant, ctx.oidc_issuer, ctx.without_auth)
    if sum(map(bool, choices)) > 1:
        raise click.ClickException(
            "Use only one of --auth0-tenant, --oidc-issuer and --without-auth."
        )
    if not ctx.oidc_issuer and (ctx.oidc_client_id or ctx.oidc_client_secret_stdin):
        raise click.ClickException("OIDC client options require --oidc-issuer.")
    if ctx.kind == "pitch" and (ctx.auth0_tenant or ctx.oidc_issuer):
        raise click.ClickException("Application login requires --kind fullstack.")
    if ctx.kind == "pitch" or ctx.without_auth:
        return
    if ctx.auth0_tenant:
        check_auth0_login(ctx.auth0_tenant)
        return
    capability = template_authentication(ctx.template_source, ctx.template_version)
    if capability and not ctx.oidc_issuer:
        _choose_provider(ctx, capability)
    if ctx.oidc_issuer:
        if capability != "oidc":
            raise click.ClickException(
                "This template revision does not support OIDC setup."
            )
        _prepare_oidc(ctx)
    elif ctx.auth0_tenant:
        check_auth0_login(ctx.auth0_tenant)
