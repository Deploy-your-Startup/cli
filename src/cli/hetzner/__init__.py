"""
Hetzner Cloud browser automation for the startup CLI.

Provides browser-based workflows for:
- Cloud Console: Account registration, project creation, API token generation
- Robot: Domain registration with contact handles and nameserver setup

Requires Playwright, which ships with a normal CLI install but is pruned inside
project deployments (they never run the browser flows).
"""

from __future__ import annotations

import asyncio
import importlib.util

import click


def _check_playwright() -> bool:
    """Check if playwright is importable."""
    return importlib.util.find_spec("playwright") is not None


def _require_playwright():
    """Raise a clear error if playwright is not installed."""
    if not _check_playwright():
        raise click.ClickException(
            "Browser automation requires Playwright. It ships with a normal\n"
            "CLI install but is pruned inside project deployments. Install it with:\n"
            "  uv pip install playwright   (or reinstall deploy-your-startup-cli)\n"
            "  playwright install chromium"
        )


# ── Public API ───────────────────────────────────────────────────────


def get_or_create_token(
    project_name: str,
    token_name: str = "deploy-cli",
    headless: bool = False,
    register: bool = False,
    email: str | None = None,
) -> str | None:
    """
    Run the full Cloud Console browser flow and return the API token.

    Steps: Login (or Register) -> Create Project -> Create API Token

    Args:
        project_name: Name for the Hetzner Cloud project.
        token_name: Description for the API token.
        headless: Run browser in headless mode (not recommended).
        register: If True, guide through account registration first.
        email: Email for pre-filling the registration form.

    Returns:
        The API token as a string, or None if the flow was cancelled/failed.
    """
    _require_playwright()
    return asyncio.run(
        _async_get_or_create_token(
            project_name=project_name,
            token_name=token_name,
            headless=headless,
            register=register,
            email=email,
        )
    )


def register_domain(
    domain: str,
    headless: bool = False,
    nameservers: list[str] | None = None,
) -> bool:
    """
    Run the Robot browser flow to register a domain.

    Steps: Login -> Ensure Handles -> Register Domain -> Set Nameservers

    Args:
        domain: The domain to register (e.g. "example.com").
        headless: Run browser in headless mode (not recommended).
        nameservers: Override the default Hetzner nameservers (e.g. the
            Cloudflare-assigned NS) to delegate DNS at registration time.

    Returns:
        True if the registration was initiated, False otherwise.
    """
    _require_playwright()
    return asyncio.run(
        _async_register_domain(
            domain=domain, headless=headless, nameservers=nameservers
        )
    )


def set_domain_nameservers(
    domain: str,
    nameservers: list[str],
    headless: bool = False,
) -> bool:
    """
    Switch an already-registered KonsoleH domain to custom nameservers.

    Args:
        domain: The domain to update (e.g. "example.com").
        nameservers: The nameservers to delegate to (e.g. Cloudflare's).
        headless: Run browser in headless mode (not recommended).

    Returns:
        True if the change was submitted, False otherwise.
    """
    _require_playwright()
    return asyncio.run(
        _async_set_domain_nameservers(
            domain=domain, nameservers=nameservers, headless=headless
        )
    )


# ── Async implementations ────────────────────────────────────────────


async def _async_get_or_create_token(
    *,
    project_name: str,
    token_name: str,
    headless: bool,
    register: bool,
    email: str | None,
) -> str | None:
    from . import _output as ui
    from .automation import HetznerAutomation
    from .credentials import save_token

    async with HetznerAutomation(headless=headless) as bot:
        # Step 1: Account
        if register:
            ui.step(1, "Account Registration")
            if not email:
                email = ui.ask("Email address")
            ok = await bot.register_account(email)
            if not ok:
                ui.error("Registration could not be completed.")
                if not ui.confirm("Try to continue anyway?"):
                    return None
            else:
                ui.success("Registration successful!")
        else:
            ui.step(1, "Login")
            ok = await bot.login()
            if not ok:
                ui.error("Login could not be completed.")
                if not ui.confirm("Try to continue anyway?"):
                    return None
            else:
                ui.success("Login successful!")

        # Step 2: Project — create directly, no confirmation needed
        ui.step(2, "Create Project")
        ok = await bot.create_project(project_name)
        if ok:
            ui.success(f'Project "{project_name}" created!')
        else:
            ui.error("Project creation failed.")
            ui.info("Please select the desired project manually in the browser.")
            ui.ask("Press Enter when ready", default="")

        # Step 3: API Token
        ui.step(3, "Create API Token")
        token = await bot.create_api_token(token_name=token_name)

        if token:
            ui.success("API token created successfully!")
            save_token(token, project_name, token_name)
            return token
        else:
            ui.error("Token could not be created/extracted.")
            manual_token = ui.ask(
                "Enter token manually (or leave empty to abort)", password=True
            )
            if manual_token:
                save_token(manual_token, project_name, token_name)
                return manual_token
            return None


async def _async_register_domain(
    *,
    domain: str,
    headless: bool,
    nameservers: list[str] | None = None,
) -> bool:
    from . import _output as ui
    from .robot import HetznerKonsoleHAutomation

    async with HetznerKonsoleHAutomation(headless=headless) as bot:
        # Step 1: Login to KonsoleH
        ui.step(1, "KonsoleH Login")
        ok = await bot.login()
        if not ok:
            ui.error("KonsoleH login could not be completed.")
            if not ui.confirm("Try to continue anyway?"):
                return False
        else:
            ui.success("KonsoleH login successful!")

        # Step 2: Register domain
        ui.step(2, "Register Domain")
        ok = await bot.register_domain(domain, nameservers=nameservers)
        if ok:
            ui.success(f'Domain "{domain}" registration initiated!')
        else:
            ui.error("Domain registration failed.")

        return ok


async def _async_set_domain_nameservers(
    *,
    domain: str,
    nameservers: list[str],
    headless: bool,
) -> bool:
    from . import _output as ui
    from .robot import HetznerKonsoleHAutomation

    async with HetznerKonsoleHAutomation(headless=headless) as bot:
        ui.step(1, "KonsoleH Login")
        ok = await bot.login()
        if not ok:
            ui.error("KonsoleH login could not be completed.")
            if not ui.confirm("Try to continue anyway?"):
                return False
        else:
            ui.success("KonsoleH login successful!")

        ui.step(2, "Change Nameservers")
        return await bot.set_nameservers(domain, nameservers)
