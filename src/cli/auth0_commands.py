"""Project-specific Auth0 apps; credentials only travel through captured stdin."""

from __future__ import annotations

import base64
import json
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import click

from cli.vault_fields import read_vault_field, store_vault_fields


def auth0_json(*args: str):
    result = subprocess.run(
        ["auth0", *args, "--json", "--no-input"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise click.ClickException(
            "Auth0 request failed. Run `auth0 login` again and "
            "check the selected tenant. Sensitive output has been withheld."
        )
    return json.loads(result.stdout)


def check_auth0_login(tenant: str) -> None:
    """Check tenant access; in a terminal, run `auth0 login` once if it fails."""
    if shutil.which("auth0") is None:
        raise click.ClickException(
            "Auth0 CLI is missing: run `brew install auth0/auth0-cli/auth0`, then `auth0 login`."
        )
    try:
        auth0_json("apps", "list", "--tenant", tenant)
    except click.ClickException:
        if not (sys.stdin.isatty() and sys.stderr.isatty()):
            raise
        click.echo(f"No usable Auth0 login for {tenant}; starting `auth0 login`.")
        if subprocess.run(["auth0", "login"], check=False).returncode:
            raise click.ClickException(
                "Auth0 login did not complete. Run `auth0 login` and try again."
            ) from None
        auth0_json("apps", "list", "--tenant", tenant)
    auth0_json("apis", "list", "--tenant", tenant)


def validate_browser_session(context, origin: str) -> None:
    """Check the session without emitting cookies, tokens or identity data."""
    identity = context.request.get(f"{origin}/oauth2/userinfo", max_redirects=0)
    private = context.request.get(f"{origin}/private_api/session", max_redirects=0)
    if identity.status != 200 or private.status != 200:
        raise click.ClickException(
            "Login returned, but the protected API is unavailable."
        )
    session_cookies = [
        cookie
        for cookie in context.cookies([origin])
        if cookie["name"].startswith("_oauth2_proxy")
    ]
    if not session_cookies or not all(
        cookie["httpOnly"] and cookie["secure"] for cookie in session_cookies
    ):
        raise click.ClickException(
            "The login session requires a secure HttpOnly cookie."
        )


def validate_live_login(origin: str, tenant: str, timeout: int) -> None:
    """Human completes login in an ephemeral browser; never persist auth state."""
    from cli.browser_runtime import launch_options
    from cli.cloudflare import _require_playwright
    from cli.playwright_errors import playwright_error

    _require_playwright()
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                **launch_options(playwright.chromium), headless=False
            )
            try:
                context = browser.new_context()
                try:
                    # Check protection and provider before asking the user to sign in.
                    for headers in ({}, {"Authorization": "Bearer invalid-test-token"}):
                        response = context.request.get(
                            f"{origin}/private_api/session",
                            headers=headers,
                            max_redirects=0,
                        )
                        if response.status not in (302, 401, 403):
                            raise click.ClickException(
                                "The private API does not correctly reject invalid credentials."
                            )
                    response = context.request.get(
                        f"{origin}/oauth2/start", max_redirects=0
                    )
                    redirect = urlsplit(response.headers.get("location", ""))
                    if (
                        response.status != 302
                        or redirect.scheme != "https"
                        or redirect.netloc != tenant
                        or parse_qs(redirect.query).get("redirect_uri")
                        != [f"{origin}/oauth2/callback"]
                    ):
                        raise click.ClickException(
                            "Login does not redirect to the selected Auth0 tenant."
                        )
                    page = context.new_page()
                    click.echo(
                        "Sign in to your new website in the browser. This check waits for your return and verifies the private API."
                    )
                    page.goto(
                        f"{origin}/oauth2/start?{urlencode({'rd': origin + '/'})}",
                        wait_until="domcontentloaded",
                    )
                    page.wait_for_url(
                        lambda url: (
                            url.startswith(origin + "/") and "/oauth2/" not in url
                        ),
                        timeout=timeout * 1000,
                    )
                    validate_browser_session(context, origin)
                finally:
                    context.close()
            finally:
                browser.close()
    except playwright_error():
        raise click.ClickException(
            "Browser login did not complete. Check the deployment and selected browser, then try again. "
            "Browser details have been withheld to protect credentials."
        ) from None
    click.echo(
        "Login verified: correct tenant, protected API and secure HttpOnly session."
    )


def configure_auth0(
    project_dir: Path, project_name: str, base_domain: str, tenant: str
) -> None:
    playbook = project_dir / "deployment" / "playbook.yml"
    has_shared_proxy = (
        playbook.is_file() and "name: oauth2-proxy" in playbook.read_text()
    )
    if (
        not (project_dir / "oauth2-proxy" / "deployment").is_dir()
        and not has_shared_proxy
    ):
        raise click.ClickException("This template has no oauth2-proxy role.")
    configuration = "\n".join(
        path.read_text()
        for path in (project_dir / "deployment" / "group_vars").glob("*.yml")
    )
    proxy_toggle = (
        "oauth2_proxy_enabled"
        if "oauth2_proxy_enabled:" in configuration
        else "oauth_enabled"
    )
    origin = f"https://{base_domain}"
    callback = f"{origin}/oauth2/callback"
    apps = auth0_json("apps", "list", "--tenant", tenant)
    existing = next(
        (
            app
            for app in apps
            if app["name"] == project_name and callback in app.get("callbacks", [])
        ),
        None,
    )
    if existing:
        app = auth0_json(
            "apps",
            "show",
            existing["client_id"],
            "--tenant",
            tenant,
            "--reveal-secrets",
        )
    else:
        app = auth0_json(
            "apps",
            "create",
            "--tenant",
            tenant,
            "--name",
            project_name,
            "--type",
            "regular",
            "--auth-method",
            "Post",
            "--grants",
            "code,refresh-token",
            "--callbacks",
            callback,
            "--logout-urls",
            origin,
            "--reveal-secrets",
        )
    audience = f"{origin}/private_api"
    apis = auth0_json("apis", "list", "--tenant", tenant)
    if not any(api.get("identifier") == audience for api in apis):
        auth0_json(
            "apis",
            "create",
            "--tenant",
            tenant,
            "--name",
            project_name,
            "--identifier",
            audience,
            "--signing-alg",
            "RS256",
            "--token-lifetime",
            "3600",
            "--offline-access=false",
            "--scopes",
            "read:session",
        )
    target = (project_dir / "deployment" / "group_vars" / "production.yml").resolve()
    cookie_secret = (
        read_vault_field(target, "oauth2_proxy_cookie_secret")
        or base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
    )
    if len(cookie_secret) == 43:
        # Padding preserves the key; Ansible's decoder requires it.
        cookie_secret += "="
    values = {
        "oauth2_proxy_client_id": app["client_id"],
        "oauth2_proxy_client_secret": app["client_secret"],
        "oauth2_proxy_cookie_secret": cookie_secret,
        "auth_domain": f"https://{tenant}",
        "auth_audience": audience,
        proxy_toggle: "true",
    }
    store_vault_fields(project_dir, target, values, "Auth0")
    click.echo(
        f"Auth0 app {project_name} configured in {tenant}; credentials saved in the project vault."
    )


@click.group(name="auth0")
def auth0():
    """Configure project login with the official Auth0 CLI."""


@auth0.command("check")
@click.option("--tenant", required=True)
def check(tenant):
    """Validate Auth0 CLI access before creating cloud resources."""
    check_auth0_login(tenant)
    click.echo(f"Auth0 access to {tenant} verified.")


@auth0.command("validate")
@click.option("--tenant", required=True)
@click.option("--base-domain", required=True)
@click.option("--timeout", type=click.IntRange(1, 900), default=300, show_default=True)
def validate(tenant, base_domain, timeout):
    """Validate a real website login after deployment; user signs in in browser."""
    for name, host in (("tenant", tenant), ("base-domain", base_domain)):
        parsed = urlsplit(f"https://{host}")
        if (
            not parsed.hostname
            or parsed.netloc != host
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username
            or ":" in parsed.netloc
        ):
            raise click.BadParameter(
                "Enter a hostname without scheme, path or port.", param_hint=f"--{name}"
            )
    validate_live_login(f"https://{base_domain}", tenant, timeout)


@auth0.command("setup")
@click.option("--tenant", required=True)
@click.option("--project-name", required=True)
@click.option("--base-domain", required=True)
@click.option(
    "--project-dir", type=click.Path(path_type=Path, exists=True), default="."
)
def setup(tenant, project_name, base_domain, project_dir):
    """Create a dedicated app/API and store credentials through startup secrets."""
    check_auth0_login(tenant)
    configure_auth0(project_dir.resolve(), project_name, base_domain, tenant)
