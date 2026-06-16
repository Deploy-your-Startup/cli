"""OVH / OpenStack browser automation for the startup CLI.

Public-cloud auth on OVH is OpenStack, so the "Hetzner token" equivalent is an
OpenStack Application Credential, captured as a clouds.yaml. This package mirrors
cli.hetzner: open a real Chrome, let the user log in, create the credential, and
capture the downloaded clouds.yaml (with manual fallbacks throughout).

Requires Playwright, which ships with a normal CLI install but is pruned inside
project deployments (they never run the browser flows).
"""

from __future__ import annotations

import asyncio

import click
import yaml


def _check_playwright() -> bool:
    try:
        import playwright  # noqa: F401

        return True
    except ImportError:
        return False


def _require_playwright() -> None:
    if not _check_playwright():
        raise click.ClickException(
            "Browser automation requires Playwright. It ships with a normal\n"
            "CLI install but is pruned inside project deployments. Install it with:\n"
            "  uv pip install playwright   (or reinstall deploy-your-startup-cli)\n"
            "  playwright install chromium"
        )


def validate_and_normalize_clouds_yaml(text: str) -> str | None:
    """Validate a clouds.yaml and normalize its cloud entry name to ``ovh``.

    The Ansible roles reference ``cloud: ovh``; Horizon may export the entry under
    a different name (e.g. ``openstack``). If there is exactly one cloud and no
    ``ovh`` entry, it is renamed so the roles find it. Returns the normalized
    YAML text, or None if the content is not a usable clouds.yaml.
    """
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if not isinstance(data, dict):
        return None
    clouds = data.get("clouds")
    if not isinstance(clouds, dict) or not clouds:
        return None

    if "ovh" not in clouds and len(clouds) == 1:
        (only_name,) = list(clouds.keys())
        clouds["ovh"] = clouds.pop(only_name)

    entry = clouds.get("ovh") or next(iter(clouds.values()))
    auth = entry.get("auth") if isinstance(entry, dict) else None
    if not isinstance(auth, dict) or not auth.get("auth_url"):
        return None

    return yaml.safe_dump(data, default_flow_style=False, sort_keys=False)


# ── Public API ────────────────────────────────────────────────────────


def get_or_create_clouds_yaml(
    project_name: str,
    *,
    headless: bool = False,
) -> str | None:
    """Run the Horizon browser flow and return a normalized clouds.yaml string.

    Returns the clouds.yaml text, or None if the flow was cancelled/failed.
    """
    _require_playwright()
    return asyncio.run(
        _async_get_or_create_clouds_yaml(
            project_name=project_name,
            headless=headless,
        )
    )


async def _async_get_or_create_clouds_yaml(
    *,
    project_name: str,
    headless: bool,
) -> str | None:
    from pathlib import Path

    from cli.hetzner import _output as ui

    from .automation import OVHAutomation
    from .credentials import save_clouds_yaml

    async with OVHAutomation(headless=headless) as bot:
        ui.step(1, "Login to OpenStack Horizon")
        await bot.open_application_credentials()
        ui.info(
            "Please log in to Horizon in the browser if prompted "
            "(SSO / 2FA is handled by you)."
        )

        ui.step(2, "Create Application Credential + download clouds.yaml")
        raw = await bot.create_and_capture_clouds_yaml(
            name=f"{project_name}-deploy-cli"
        )

        if not raw:
            # Manual fallback: the user downloads clouds.yaml themselves and
            # tells us where it landed.
            ui.warning(
                "Could not capture clouds.yaml automatically. In Horizon, click "
                '"Download clouds.yaml" for the credential you created.'
            )
            path = ui.ask(
                "Path to the downloaded clouds.yaml (empty to abort)", default=""
            )
            if not path:
                return None
            raw = Path(path).expanduser().read_text(encoding="utf-8")

    normalized = validate_and_normalize_clouds_yaml(raw)
    if not normalized:
        from cli.hetzner import _output as ui

        ui.error("The captured file is not a valid clouds.yaml (missing auth_url).")
        return None

    save_clouds_yaml(normalized)
    return normalized
