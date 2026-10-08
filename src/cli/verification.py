"""Read-only verification of a generated full-stack project's first deployment."""

from __future__ import annotations

import ipaddress
import json
import re
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

import click
import httpx
import yaml


def _github_runs(repo: str, workflow: str) -> list[dict]:
    result = subprocess.run(
        [
            "gh",
            "api",
            f"repos/{repo}/actions/workflows/{workflow}/runs?branch=main&per_page=20",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise click.ClickException(
            "Cannot read GitHub Actions. Check gh login and actions access."
        )
    try:
        runs = json.loads(result.stdout)["workflow_runs"]
        return [run for run in runs if run.get("event") != "pull_request"]
    except (ValueError, KeyError, TypeError):
        raise click.ClickException(
            "GitHub returned an unreadable workflow status."
        ) from None


def _server_address(project: Path, project_name: str) -> tuple[str, str | None] | None:
    token_file = project / "deployment/hcloud_token_production"
    if not token_file.is_file():
        raise click.ClickException(
            "No encrypted Hetzner token found. Pass --server-ip for an existing server."
        )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "secrets",
            "get-file",
            "--file",
            str(token_file),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise click.ClickException(
            "Cannot read the Hetzner token through startup. Check the project Keychain entry."
        )
    try:
        with httpx.Client(timeout=15) as client:
            headers = {"Authorization": f"Bearer {result.stdout.strip()}"}
            # Use the load balancer when the deployment created one.
            for resource in ("load_balancers", "servers"):
                response = client.get(
                    f"https://api.hetzner.cloud/v1/{resource}", headers=headers
                )
                response.raise_for_status()
                for item in response.json()[resource]:
                    expected_name = project_name + (
                        "-master-0" if resource == "servers" else ""
                    )
                    if item["name"] == expected_name:
                        network = item["public_net"]
                        return network["ipv4"]["ip"], (network.get("ipv6") or {}).get(
                            "ip"
                        )
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        raise click.ClickException(
            "Cannot determine the server IP from Hetzner. No credentials were printed."
        ) from None
    return None


def verify_project(
    project: Path,
    *,
    timeout: int = 0,
    server_ip: str | None = None,
    health_path: str = "/api/health",
) -> None:
    """Require successful Actions, matching DNS and a healthy HTTPS endpoint."""
    project = project.resolve()
    try:
        answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
        domain = answers["base_domain"]
        name = answers["project_name"]
        repo = f"{answers['github_username']}/{name}"
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        raise click.ClickException(
            "Run verification in a generated project with .copier-answers.yml."
        ) from None
    if not isinstance(domain, str) or not re.fullmatch(r"[A-Za-z0-9.-]+", domain):
        raise click.ClickException("The project has an invalid base_domain.")
    if not health_path.startswith("/") or health_path.startswith("//"):
        raise click.ClickException("--health-path must be a path starting with /.")
    byos = (project / "deployment/inventory.byos.yml").is_file()
    if byos and not server_ip:
        raise click.ClickException("Pass --server-ip to verify a BYOS deployment.")
    workflows = [
        "deploy.yml",
        *sorted(
            path.name
            for path in (project / ".github/workflows").glob("build-and-deploy-*.yml")
        ),
    ]
    if not byos:
        workflows.insert(0, "deploy-infrastructure.yml")
    deadline = time.monotonic() + timeout
    shown_ip = None
    while True:
        problems = []
        failed = False
        for workflow in workflows:
            runs = _github_runs(repo, workflow)
            if not runs or runs[0]["status"] != "completed":
                problems.append(f"Actions pending: {workflow}")
            elif runs[0].get("conclusion") != "success":
                failed = True
                problems.append(
                    f"Actions failed: {workflow} ({runs[0].get('conclusion')}) — {runs[0].get('html_url', '')}"
                )
        target = (server_ip, None) if server_ip else _server_address(project, name)
        address, ipv6 = target if target else (None, None)
        if not address:
            problems.append("Server IP is not allocated yet.")
        else:
            if shown_ip != address:
                click.echo(f"DNS required: {domain} A {address}")
                click.echo(
                    "Update this host's record at its authoritative DNS provider; preserve other hosts."
                )
                shown_ip = address
            try:
                addresses = {
                    str(item[4][0])
                    for item in socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)
                }
            except OSError:
                addresses = set()
            # A stale AAAA record can route browsers away from the server too.
            if address not in addresses or any(
                value != address for value in addresses if ":" not in value
            ):
                problems.append(
                    f"DNS mismatch: {domain} resolves to {', '.join(sorted(addresses)) or 'nothing'}, expected {address}."
                )
            ipv6_network = ipaddress.ip_network(ipv6, strict=False) if ipv6 else None
            if any(
                ":" in value
                and (
                    ipv6_network is None
                    or ipaddress.ip_address(value) not in ipv6_network
                )
                for value in addresses
            ):
                problems.append(
                    "DNS mismatch: an AAAA record does not match the expected server's IPv6 network. Correct or remove the stale record."
                )
            if not any(problem.startswith("DNS") for problem in problems):
                try:
                    response = httpx.get(
                        f"https://{domain}{health_path}",
                        timeout=15,
                        follow_redirects=False,
                    )
                    if (
                        response.status_code != 200
                        or response.json().get("status") != "ok"
                    ):
                        problems.append(
                            "HTTPS health check did not return 200 with status=ok."
                        )
                except (httpx.HTTPError, ValueError, AttributeError):
                    problems.append(
                        "HTTPS health check failed (connection, certificate or response)."
                    )
        if not problems:
            click.echo(
                f"Verified live: https://{domain} — Actions, DNS, TLS and application health are OK."
            )
            return
        for problem in problems:
            click.echo(problem)
        if failed or time.monotonic() >= deadline:
            command = "startup verify --working-directory " + shlex.quote(str(project))
            raise click.ClickException(
                f"Project configured; deployment verification is incomplete. Resolve the checks above, then run: {command}"
            )
        click.echo(
            "Waiting for deployment and DNS; checking again in up to 15 seconds ..."
        )
        time.sleep(min(15, max(0, deadline - time.monotonic())))


@click.command()
@click.option(
    "--working-directory",
    type=click.Path(file_okay=False, exists=True, path_type=Path),
    default=".",
)
@click.option(
    "--timeout",
    type=click.IntRange(min=0),
    default=0,
    show_default=True,
    help="Seconds to wait for deployment and DNS.",
)
@click.option(
    "--server-ip", help="Expected public IPv4 for BYOS or custom infrastructure."
)
@click.option("--health-path", default="/api/health", show_default=True)
def verify(
    working_directory: Path, timeout: int, server_ip: str | None, health_path: str
):
    """Check Actions, server IP, DNS, TLS and application health without changes."""
    verify_project(
        working_directory, timeout=timeout, server_ip=server_ip, health_path=health_path
    )
