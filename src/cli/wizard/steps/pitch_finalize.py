"""Step 4 (pitch): commit, create GH repo, push CF secrets, push code."""

from __future__ import annotations

import subprocess

import httpx

from cli import wizard_output as ui
from cli.sync_commands import _run_command

from ..base import WizardStep, is_pushed, repo_exists
from ..context import BootstrapContext


def ensure_pages_project(ctx: BootstrapContext) -> None:
    """Create the Cloudflare Pages project if it does not exist yet."""
    headers = {
        "Authorization": f"Bearer {ctx.cloudflare_api_token}",
        "Content-Type": "application/json",
    }
    base_url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{ctx.cloudflare_account_id}/pages/projects"
    )

    get_resp = httpx.get(
        f"{base_url}/{ctx.project_name}",
        headers=headers,
        timeout=20,
    )
    if get_resp.status_code == 200 and get_resp.json().get("success"):
        ui.action_done("Cloudflare Pages Projekt bereits vorhanden")
        return

    if get_resp.status_code not in {404, 400}:
        raise RuntimeError(
            f"Cloudflare Pages Projekt konnte nicht geprüft werden: {get_resp.text}"
        )

    payload = {
        "name": ctx.project_name,
        "production_branch": "main",
    }
    create_resp = httpx.post(
        base_url,
        headers=headers,
        json=payload,
        timeout=20,
    )
    data = create_resp.json()
    if create_resp.status_code in {200, 201} and data.get("success"):
        ui.action_done("Cloudflare Pages Projekt erstellt")
        return

    raise RuntimeError(f"Cloudflare Pages Projekt konnte nicht erstellt werden: {data}")


def pages_project_exists(ctx: BootstrapContext) -> bool:
    """Return True when the Cloudflare Pages project already exists."""
    if not ctx.cloudflare_api_token or not ctx.cloudflare_account_id:
        return False

    headers = {
        "Authorization": f"Bearer {ctx.cloudflare_api_token}",
        "Content-Type": "application/json",
    }
    base_url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{ctx.cloudflare_account_id}/pages/projects/{ctx.project_name}"
    )
    try:
        resp = httpx.get(base_url, headers=headers, timeout=20)
        return resp.status_code == 200 and resp.json().get("success")
    except httpx.HTTPError:
        return False


def _pages_target(ctx: BootstrapContext) -> str:
    """Return the project's pages.dev hostname (CNAME target for custom domains)."""
    fallback = f"{ctx.project_name}.pages.dev"
    if not ctx.cloudflare_api_token or not ctx.cloudflare_account_id:
        return fallback
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{ctx.cloudflare_account_id}/pages/projects/{ctx.project_name}"
    )
    try:
        resp = httpx.get(
            url,
            headers={"Authorization": f"Bearer {ctx.cloudflare_api_token}"},
            timeout=20,
        )
        if resp.status_code == 200 and resp.json().get("success"):
            subdomain = resp.json()["result"].get("subdomain")
            if subdomain:
                return subdomain
    except (httpx.HTTPError, KeyError, ValueError):
        pass
    return fallback


def custom_domain_linked(ctx: BootstrapContext) -> bool:
    """Return True only when the apex domain is attached AND active.

    Attaching leaves the domain "pending"/"initializing" until the CNAME exists,
    so we require an active status — otherwise a re-run would skip the step that
    creates the DNS record.
    """
    if not ctx.cloudflare_api_token or not ctx.cloudflare_account_id:
        return False
    base_url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{ctx.cloudflare_account_id}/pages/projects/{ctx.project_name}"
        f"/domains/{ctx.base_domain}"
    )
    try:
        resp = httpx.get(
            base_url,
            headers={"Authorization": f"Bearer {ctx.cloudflare_api_token}"},
            timeout=20,
        )
        if resp.status_code != 200 or not resp.json().get("success", False):
            return False
        return resp.json().get("result", {}).get("status") == "active"
    except (httpx.HTTPError, ValueError):
        return False


class PitchFinalizeStep(WizardStep):
    number = 4
    name = "Abschluss"

    def check(self, ctx: BootstrapContext) -> bool:
        if not ctx.project_dir.exists():
            return False
        # Only skip when the domain is ALSO linked — otherwise a re-run after an
        # earlier push would never reach the custom-domain step in run().
        if (
            is_pushed(ctx.project_dir)
            and repo_exists(ctx.full_repo)
            and pages_project_exists(ctx)
            and custom_domain_linked(ctx)
        ):
            ui.skip_indicator("Code gepusht & Custom Domain verknüpft")
            return True
        return False

    def run(self, ctx: BootstrapContext) -> None:
        ui.action_start("Code committen...")
        _run_command(["git", "add", "-A"], cwd=ctx.project_dir)
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ctx.project_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if status.stdout.strip():
            _run_command(
                ["git", "commit", "-m", "bootstrap: configure project"],
                cwd=ctx.project_dir,
            )
            ui.action_done("Committed")
        else:
            ui.action_done("Nichts zu committen")

        subprocess.run(
            ["git", "remote", "remove", "origin"],
            cwd=ctx.project_dir,
            capture_output=True,
            check=False,
        )
        if not repo_exists(ctx.full_repo):
            ui.action_start("GitHub-Repository erstellen...")
            _run_command(
                ["gh", "repo", "create", ctx.full_repo, "--private", "--source", "."],
                cwd=ctx.project_dir,
            )
            ui.action_done("Repository erstellt")
        else:
            _run_command(
                [
                    "git",
                    "remote",
                    "add",
                    "origin",
                    f"https://github.com/{ctx.full_repo}.git",
                ],
                cwd=ctx.project_dir,
            )

        ui.action_start("Cloudflare Secrets setzen...")
        token, account_id = ctx.require_cloudflare()
        _run_command(
            [
                "gh",
                "secret",
                "set",
                "CLOUDFLARE_API_TOKEN",
                "--body",
                token,
            ],
            cwd=ctx.project_dir,
            capture_output=True,
        )
        _run_command(
            [
                "gh",
                "secret",
                "set",
                "CLOUDFLARE_ACCOUNT_ID",
                "--body",
                account_id,
            ],
            cwd=ctx.project_dir,
            capture_output=True,
        )
        ui.action_done("Secrets gesetzt")

        ui.action_start("Cloudflare Pages Projekt sicherstellen...")
        ensure_pages_project(ctx)

        ui.action_start("Push nach GitHub...")
        _run_command(
            ["git", "push", "-u", "origin", "main"],
            cwd=ctx.project_dir,
            capture_output=True,
        )
        ui.action_done("Gepusht")

        self._link_custom_domain(ctx)

    def _link_custom_domain(self, ctx: BootstrapContext) -> None:
        """Attach apex + www to Pages AND create the CNAME records.

        Attaching via the Pages API does not create the DNS record (only the
        dashboard does that). Without the CNAME the domain stays "Requires DNS
        setup", so we create the proxied CNAME → <project>.pages.dev ourselves.
        """
        from cli.cloudflare_zones import (
            add_pages_custom_domain,
            clear_conflicting_records,
            ensure_cname_record,
        )

        token, account_id = ctx.require_cloudflare()
        apex = ctx.base_domain
        www = f"www.{apex}"
        target = _pages_target(ctx)

        ui.action_start(f"Custom Domain {apex} mit Pages verknüpfen...")
        try:
            # Cloudflare auto-imports existing records on zone creation; remove
            # any apex/www A/AAAA/CNAME so the Pages domain can own those hosts.
            if ctx.cloudflare_zone_id:
                removed = clear_conflicting_records(
                    token, ctx.cloudflare_zone_id, [apex, www]
                )
                if removed:
                    ui.info(f"{removed} kollidierende DNS-Records entfernt")

            for host in (apex, www):
                add_pages_custom_domain(
                    token,
                    account_id,
                    ctx.project_name,
                    host,
                )
                if ctx.cloudflare_zone_id:
                    ensure_cname_record(
                        token,
                        ctx.cloudflare_zone_id,
                        host,
                        target,
                    )
            ui.action_done(f"Custom Domains verknüpft ({apex}, {www})")
            ui.info(
                f"CNAME → {target} gesetzt; Cloudflare aktiviert DNS + TLS "
                "automatisch (kann nach NS-Propagation ein paar Minuten dauern)."
            )
        except (RuntimeError, httpx.HTTPError) as exc:
            ui.action_fail("Custom Domain konnte nicht automatisch verknüpft werden")
            ui.warning(str(exc))
            ui.info(
                f"Bitte manuell verknüpfen:\n"
                f"  https://dash.cloudflare.com → Workers & Pages → {ctx.project_name} "
                "→ Custom domains → Set up a custom domain"
            )
