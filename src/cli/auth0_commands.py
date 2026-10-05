"""Project-specific Auth0 apps; credentials only travel through captured stdin."""

from __future__ import annotations

import base64
import json
import secrets
import shutil
import subprocess
from pathlib import Path

import click


def auth0_json(*args: str):
    result = subprocess.run(
        ["auth0", *args, "--json", "--no-input"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise click.ClickException(
            "Auth0-Aufruf fehlgeschlagen. Bitte `auth0 login` erneuern und "
            "den gewählten Tenant prüfen. Sensible Ausgabe wurde zurückgehalten."
        )
    return json.loads(result.stdout)


def check_auth0_login(tenant: str) -> None:
    if shutil.which("auth0") is None:
        raise click.ClickException(
            "Auth0 CLI fehlt: `brew install auth0/auth0-cli/auth0`, dann `auth0 login`."
        )
    auth0_json("apps", "list", "--tenant", tenant)


def configure_auth0(
    project_dir: Path, project_name: str, base_domain: str, tenant: str
) -> None:
    if not (project_dir / "oauth2-proxy" / "deployment").is_dir():
        raise click.ClickException("Das Template enthält keine oauth2-proxy-Rolle.")
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
    cookie = subprocess.run(
        [
            "startup",
            "secrets",
            "get-field",
            "-f",
            str(target),
            "--field",
            "oauth2_proxy_cookie_secret",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    cookie_secret = (
        cookie.stdout.strip()
        if cookie.returncode == 0
        else base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()
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
        "oauth_enabled": "true",
    }
    for field, value in values.items():
        current = subprocess.run(
            ["startup", "secrets", "get-field", "-f", str(target), "--field", field],
            text=True,
            capture_output=True,
            check=False,
        )
        if current.returncode == 0 and current.stdout.strip() == value:
            continue
        command = [
            "startup",
            "secrets",
            "update",
            "-r",
            str(target),
            "--field-stdin",
            field,
            "--create-in",
            str(target),
        ]
        for dry_run in (True, False):
            result = subprocess.run(
                command + (["--dry-run"] if dry_run else []),
                input=value,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode:
                raise click.ClickException(
                    "Auth0-Konfiguration konnte nicht im Vault gespeichert werden."
                )
    click.echo(
        f"Auth0-App {project_name} in {tenant} konfiguriert; Zugangsdaten im Projekt-Vault."
    )


@click.group(name="auth0")
def auth0():
    """Configure project login with the official Auth0 CLI."""


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
