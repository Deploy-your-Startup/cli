"""Step 4 (pitch): commit, create GH repo, store CF secrets in production, push code."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import click
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
        ui.action_done("Cloudflare Pages project already exists")
        return

    if get_resp.status_code not in {404, 400}:
        raise RuntimeError(f"Could not check Cloudflare Pages project: {get_resp.text}")

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
        ui.action_done("Cloudflare Pages project created")
        return

    raise RuntimeError(f"Could not create Cloudflare Pages project: {data}")


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


PAGES_ENVIRONMENT = "production"
CLOUDFLARE_SECRETS = ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID")


def _gh(
    args: list[str], cwd: Path | None = None, stdin: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run gh without echoing stdin or output; callers decide what failure means."""
    return subprocess.run(
        ["gh", *args],
        cwd=cwd,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )


def _is_public(full_repo: str, cwd: Path | None = None) -> bool:
    visibility = _gh(
        ["repo", "view", full_repo, "--json", "visibility", "--jq", ".visibility"],
        cwd,
    )
    if visibility.returncode:
        raise click.ClickException(f"Could not read the visibility of {full_repo}.")
    return visibility.stdout.strip().upper() == "PUBLIC"


def public_repository_lacks_environment(full_repo: str) -> bool:
    """True when a public repository still needs the production environment."""
    if not _is_public(full_repo):
        return False
    environment = _gh(["api", f"repos/{full_repo}/environments/{PAGES_ENVIRONMENT}"])
    return environment.returncode != 0


def _ensure_production_environment(project_dir: Path, full_repo: str) -> None:
    """Create the environment with a deployment branch policy for main only."""
    environment = f"repos/{full_repo}/environments/{PAGES_ENVIRONMENT}"
    created = _gh(
        ["api", "--method", "PUT", environment, "--input", "-"],
        project_dir,
        stdin=json.dumps(
            {
                "deployment_branch_policy": {
                    "protected_branches": False,
                    "custom_branch_policies": True,
                }
            }
        ),
    )
    if created.returncode:
        raise click.ClickException(
            f"Could not create the {PAGES_ENVIRONMENT} environment."
        )

    policies = _gh(
        [
            "api",
            f"{environment}/deployment-branch-policies",
            "--jq",
            '.branch_policies[] | select(.type != "tag") | .name',
        ],
        project_dir,
    )
    if policies.returncode:
        raise click.ClickException("Could not read the deployment branch policies.")
    if "main" not in policies.stdout.split():
        added = _gh(
            [
                "api",
                "--method",
                "POST",
                f"{environment}/deployment-branch-policies",
                "-f",
                "name=main",
                "-f",
                "type=branch",
            ],
            project_dir,
        )
        if added.returncode:
            raise click.ClickException("Could not limit the environment to main.")


def _remove_repository_secrets(project_dir: Path, full_repo: str) -> None:
    """Drop repository copies so other branches cannot read the credentials."""
    listed = _gh(
        ["secret", "list", "--repo", full_repo, "--json", "name", "--jq", ".[].name"],
        project_dir,
    )
    if listed.returncode:
        raise click.ClickException("Could not list the repository secrets.")
    for name in set(CLOUDFLARE_SECRETS) & set(listed.stdout.split()):
        deleted = _gh(["secret", "delete", name, "--repo", full_repo], project_dir)
        if deleted.returncode:
            raise click.ClickException(
                f"Could not remove the repository secret {name}."
            )


def save_cloudflare_secrets(
    project_dir: Path, full_repo: str, token: str, account_id: str
) -> bool:
    """Store the Cloudflare secrets where the deploy workflow reads them.

    Private repositories keep repository secrets, because environments there
    need a paid GitHub plan. Public repositories get a production environment
    that only main can deploy to, lose the repository copies and require
    approval for fork PR workflows. Values go through stdin, never argv or
    errors. Returns True when the secrets live in the environment.
    """
    public = _is_public(full_repo, project_dir)
    if public:
        _ensure_production_environment(project_dir, full_repo)
    scope = ["--env", PAGES_ENVIRONMENT] if public else []
    for name, value in zip(CLOUDFLARE_SECRETS, (token, account_id), strict=True):
        stored = _gh(
            ["secret", "set", name, "--repo", full_repo, *scope],
            project_dir,
            stdin=value,
        )
        if stored.returncode:
            raise click.ClickException(f"Could not set the GitHub secret {name}.")
    if public:
        _remove_repository_secrets(project_dir, full_repo)
        approval = _gh(
            [
                "api",
                "--method",
                "PUT",
                f"repos/{full_repo}/actions/permissions/fork-pr-contributor-approval",
                "-f",
                "approval_policy=all_external_contributors",
            ],
            project_dir,
        )
        if approval.returncode:
            raise click.ClickException(
                "Could not require approval for fork PR workflows."
            )
    return public


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
            and not public_repository_lacks_environment(ctx.full_repo)
        ):
            ui.skip_indicator("Code pushed and custom domain connected")
            return True
        return False

    def run(self, ctx: BootstrapContext) -> None:
        ui.action_start("Committing project...")
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
            ui.action_done("Nothing to commit")

        subprocess.run(
            ["git", "remote", "remove", "origin"],
            cwd=ctx.project_dir,
            capture_output=True,
            check=False,
        )
        if not repo_exists(ctx.full_repo):
            ui.action_start("Creating GitHub repository...")
            _run_command(
                ["gh", "repo", "create", ctx.full_repo, "--private", "--source", "."],
                cwd=ctx.project_dir,
            )
            ui.action_done("Repository created")
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

        ui.action_start("Saving Cloudflare secrets...")
        token, account_id = ctx.require_cloudflare()
        if save_cloudflare_secrets(ctx.project_dir, ctx.full_repo, token, account_id):
            ui.action_done(f"Secrets saved in the {PAGES_ENVIRONMENT} environment")
        else:
            ui.action_done("Secrets saved as repository secrets")

        ui.action_start("Checking Cloudflare Pages project...")
        ensure_pages_project(ctx)

        ui.action_start("Pushing to GitHub...")
        _run_command(
            ["git", "push", "-u", "origin", "main"],
            cwd=ctx.project_dir,
            capture_output=True,
        )
        ui.action_done("Pushed")

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

        ui.action_start(f"Custom Domain {apex}: connecting to Pages...")
        try:
            # Cloudflare auto-imports existing records on zone creation; remove
            # any apex/www A/AAAA/CNAME so the Pages domain can own those hosts.
            if ctx.cloudflare_zone_id:
                removed = clear_conflicting_records(
                    token, ctx.cloudflare_zone_id, [apex, www]
                )
                if removed:
                    ui.info(f"{removed} conflicting DNS records removed")

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
            ui.action_done(f"Custom domains connected ({apex}, {www})")
            ui.info(
                f"CNAME → {target} gesetzt; Cloudflare aktiviert DNS + TLS "
                "automatisch (kann nach a few minutes after nameserver propagation)."
            )
        except (RuntimeError, httpx.HTTPError) as exc:
            ui.action_fail("Could not connect the custom domain automatically")
            ui.warning(str(exc))
            ui.info(
                f"Connect it manually:\n"
                f"  https://dash.cloudflare.com → Workers & Pages → {ctx.project_name} "
                "→ Custom domains → Set up a custom domain"
            )
