"""The Sentry login: a personal token kept in ~/.sentryclirc like sentry-cli.

Sentry has no device login, and `sentry-cli login` leads to organization
tokens, which only carry org:ci. A terminal without a usable token is sent to
the personal token page instead; the pasted token is read hidden, checked
against the API and saved only once it has the scopes setup needs. It is never
printed or passed as an argument. SENTRY_AUTH_TOKEN, SENTRY_URL and SENTRY_ORG
override the file, as they do for sentry-cli.
"""

from __future__ import annotations

import configparser
import os
import sys
from pathlib import Path

import click
import httpx

DEFAULT_URL = "https://sentry.io"
SCOPES = ("org:read", "team:read", "project:read", "project:write")
TOKEN_PAGE = "/settings/account/api/auth-tokens/"
LOGIN_HINT = (
    f"Create a personal token at {DEFAULT_URL}{TOKEN_PAGE} "
    f"(EU organizations: https://de.sentry.io{TOKEN_PAGE}) with "
    f"{', '.join(SCOPES)}, then run `startup sentry check` in a terminal "
    "or set SENTRY_AUTH_TOKEN."
)


def sentryclirc_path() -> Path:
    return Path.home() / ".sentryclirc"


class _CaseKeepingParser(configparser.ConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return optionstr


def sentryclirc() -> configparser.ConfigParser:
    config = _CaseKeepingParser(interpolation=None)
    config.read(sentryclirc_path())
    return config


def saved_token(config: configparser.ConfigParser) -> str:
    return os.environ.get("SENTRY_AUTH_TOKEN") or config.get(
        "auth", "token", fallback=""
    )


def sentry_url(config: configparser.ConfigParser) -> str:
    url = os.environ.get("SENTRY_URL") or config.get(
        "defaults", "url", fallback=DEFAULT_URL
    )
    return url.rstrip("/")


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stderr.isatty()


def _granted(scope: str, scopes: set[str]) -> bool:
    # Sentry's higher levels include the lower ones.
    kind, level = scope.split(":")
    stronger = {"read": ("read", "write", "admin"), "write": ("write", "admin")}
    return any(f"{kind}:{grade}" in scopes for grade in stronger[level])


def missing_scopes(url: str, token: str) -> list[str] | None:
    """Return the scopes the token lacks, or None when Sentry rejects it."""
    try:
        response = httpx.get(
            f"{url}/api/0/", headers={"Authorization": f"Bearer {token}"}, timeout=30
        )
    except httpx.HTTPError:
        raise click.ClickException(
            f"Sentry is unreachable at {url}. Check the network and URL."
        ) from None
    if response.status_code in (401, 403):
        return None
    if response.is_error:
        raise click.ClickException(
            f"Sentry request failed with HTTP {response.status_code}. "
            "The response has been withheld."
        )
    auth = response.json().get("auth")
    if not auth:
        return None
    granted: set[str] = set(auth.get("scopes") or ())
    return [scope for scope in SCOPES if not _granted(scope, granted)]


def _only_org(url: str, token: str) -> str | None:
    try:
        response = httpx.get(
            f"{url}/api/0/organizations/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
    except httpx.HTTPError:
        return None
    if response.is_error:
        return None
    orgs = response.json()
    return orgs[0]["slug"] if len(orgs) == 1 else None


def save_login(url: str, token: str, org: str | None) -> None:
    """Write [auth] token (and defaults) as sentry-cli does, keeping the rest."""
    config = sentryclirc()
    if not config.has_section("auth"):
        config.add_section("auth")
    config.set("auth", "token", token)
    defaults = {"url": url if url != DEFAULT_URL else None, "org": org}
    for key, value in defaults.items():
        if value and not config.has_option("defaults", key):
            if not config.has_section("defaults"):
                config.add_section("defaults")
            config.set("defaults", key, value)
    path = sentryclirc_path()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as handle:
        config.write(handle)
    path.chmod(0o600)


def _rejection(missing: list[str] | None) -> str:
    if missing is None:
        return "Sentry rejected this token."
    return (
        f"This token lacks {', '.join(missing)}. Organization tokens only carry "
        "org:ci; create a personal token instead."
    )


def _prompt_for_token(url: str, reason: str) -> None:
    from cli.wizard.base import open_browser

    page = f"{url}{TOKEN_PAGE}"
    click.echo(
        f"{reason} Create a personal token with {', '.join(SCOPES)} at:\n  {page}"
    )
    open_browser(page, "Sentry personal tokens")
    token = click.prompt("Paste the Sentry token", hide_input=True).strip()
    missing = missing_scopes(url, token)
    if missing != []:
        raise click.ClickException(f"{_rejection(missing)} Nothing was saved.")
    save_login(url, token, os.environ.get("SENTRY_ORG") or _only_org(url, token))
    click.echo(f"Sentry token saved in {sentryclirc_path()}.")


def ensure_sentry_login() -> None:
    """In a terminal, replace a missing or insufficient saved token."""
    if os.environ.get("SENTRY_AUTH_TOKEN") or not _interactive():
        return
    config = sentryclirc()
    url = sentry_url(config)
    token = config.get("auth", "token", fallback="")
    if not token:
        _prompt_for_token(url, "No Sentry login found.")
        return
    missing = missing_scopes(url, token)
    if missing != []:
        _prompt_for_token(
            url, f"The saved Sentry login is unusable: {_rejection(missing)}"
        )
