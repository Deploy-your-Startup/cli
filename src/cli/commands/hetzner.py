"""``startup hetzner``: Hetzner account, token and domain setup."""

import click


@click.group()
def hetzner():
    """Hetzner Cloud account, project, domain & token management via browser automation."""


@hetzner.command("setup")
@click.option(
    "--headless",
    is_flag=True,
    default=False,
    help="Run browser in headless mode (not recommended)",
)
@click.option(
    "--project", "-p", default=None, help="Project name (prompted if not given)"
)
@click.option(
    "--token-name",
    "--token_name",
    "token_name",
    default="deploy-cli",
    help="Name for the API token",
)
@click.option(
    "--register",
    is_flag=True,
    default=False,
    help="Register a new account instead of logging in",
)
@click.option("--email", default=None, help="Email for account registration")
def hetzner_setup(headless, project, token_name, register, email):
    """Full interactive setup: Login/Register -> Project -> API Token."""
    from cli.hetzner import get_or_create_token

    if not project:
        project = click.prompt("  Project name")

    token = get_or_create_token(
        project_name=project,
        token_name=token_name,
        headless=headless,
        register=register,
        email=email,
    )

    if token:
        click.echo(f"\n  Token: {token[:8]}...{token[-4:]}")
        click.echo("  Use with: startup bootstrap --hetzner-token <token>")
    else:
        click.echo("\n  No token obtained.", err=True)
        raise SystemExit(1)


@hetzner.command("domain-check")
@click.argument("domain")
def hetzner_domain_check(domain):
    """Check availability and Hetzner's public annual domain rate, without buying.

    Exit codes: 0 = free, 1 = registered, 2 = unknown/error (including invalid input).
    Registration estimates include 19% German VAT; renewal/premium prices may be unknown.
    A free registry result does not guarantee Hetzner can sell the domain.
    """
    from cli.hetzner.domain_check import check_domain

    try:
        result = check_domain(domain)
    except ValueError as exc:
        click.echo(f"Invalid domain: {exc}", err=True)
        raise SystemExit(2) from exc
    click.echo(result.output())
    raise SystemExit(result.exit_code)


@hetzner.command("domain")
@click.option(
    "--headless",
    is_flag=True,
    default=False,
    help="Run browser in headless mode (not recommended)",
)
@click.option(
    "--contact-email",
    help="E-mail of the existing KonsoleH contact that becomes the domain owner.",
)
@click.option(
    "--confirm-order",
    is_flag=True,
    default=None,
    help="Place the binding order after showing its summary, without asking.",
)
@click.argument("domain", required=False)
def hetzner_domain(headless, contact_email, confirm_order, domain):
    """Register a domain via Hetzner KonsoleH.

    Selects an existing contact as owner and shows Hetzner's order summary with
    the price. An interactive terminal then asks before the binding order;
    otherwise the order is placed only with --confirm-order or by you in the
    browser, which this command waits for.
    """
    from cli.hetzner import register_domain as do_register

    if not domain:
        domain = click.prompt("  Domain to register (e.g. example.com)")

    ok = do_register(
        domain=domain,
        headless=headless,
        contact_email=contact_email,
        confirm_order=confirm_order,
    )
    if not ok:
        raise SystemExit(1)


@hetzner.command("ns")
@click.option(
    "--headless",
    is_flag=True,
    default=False,
    help="Run browser in headless mode (not recommended)",
)
@click.argument("domain")
@click.argument("nameservers", nargs=-1, required=True)
def hetzner_ns(headless, domain, nameservers):
    """Switch an existing KonsoleH domain to custom nameservers.

    Example: startup hetzner ns example.com dana.ns.cloudflare.com rob.ns.cloudflare.com
    """
    from cli.hetzner import set_domain_nameservers

    ok = set_domain_nameservers(
        domain=domain, nameservers=list(nameservers), headless=headless
    )
    if not ok:
        raise SystemExit(1)


@hetzner.command("token")
@click.option(
    "--headless",
    is_flag=True,
    default=False,
    help="Run browser in headless mode (not recommended)",
)
@click.option(
    "--token-name",
    "--token_name",
    "token_name",
    default="deploy-cli",
    help="Name for the API token",
)
def hetzner_token(headless, token_name):
    """Create a new API token only (requires existing login session)."""
    from cli.hetzner import get_or_create_token

    project = click.prompt("  Project name (for reference)")

    token = get_or_create_token(
        project_name=project,
        token_name=token_name,
        headless=headless,
    )

    if token:
        click.echo(f"\n  Token: {token[:8]}...{token[-4:]}")
    else:
        click.echo("\n  No token obtained.", err=True)
        raise SystemExit(1)


@hetzner.command("status")
def hetzner_status():
    """Show stored token information."""
    from cli.hetzner.credentials import show_token_info

    show_token_info()


@hetzner.command("clean")
def hetzner_clean():
    """Remove stored credentials and browser state."""
    import shutil

    from cli.hetzner.config import CONFIG_DIR

    if click.confirm(f"  Delete all stored data in {CONFIG_DIR}?", default=False):
        if CONFIG_DIR.exists():
            shutil.rmtree(CONFIG_DIR)
            click.echo(f"  Deleted: {CONFIG_DIR}")
        else:
            click.echo("  Nothing to delete.")
