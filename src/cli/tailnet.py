"""Preflight for projects in private network mode.

A project with ``network_mode: private`` closes every public port; its servers
are only reachable over the tailnet. Without Tailscale on this machine the
Ansible run would not fail with a reason but with an SSH timeout half a minute
in, so the commands that reach the servers check the tailnet first and say what
is missing.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

import click

# The Mac App Store and standalone apps ship their CLI inside the bundle and do
# not put it on PATH.
TAILSCALE_APP_CLI = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"

INSTALL_HINT = (
    "Install Tailscale: https://tailscale.com/download\n"
    "  macOS:  brew install tailscale && sudo brew services start tailscale\n"
    "          (or the app: brew install --cask tailscale-app)\n"
    "  Linux:  curl -fsSL https://tailscale.com/install.sh | sh"
)

_NETWORK_MODE_LINE = re.compile(r"^network_mode:\s*(['\"]?)([A-Za-z]+)\1\s*(?:#.*)?$")


def resolve_network_mode(working_dir: Path, environment: str) -> str:
    """Read ``network_mode`` from group_vars without decrypting the vault.

    Same precedence as Ansible: ``<environment>.yml`` overrides ``all.yml``.
    Anchored at column 0, so a nested key of the same name does not count.
    """
    mode = "public"
    group_vars = working_dir / "group_vars"
    for name in ("all.yml", f"{environment}.yml"):
        path = group_vars / name
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            match = _NETWORK_MODE_LINE.match(line)
            if match:
                mode = match.group(2).lower()
    return mode


def is_private_network_host(hostvars: dict) -> bool:
    """Whether the inventory reaches this host over the tailnet.

    The hetzner-server role labels servers ``network=private`` in private network
    mode, and the inventory then sets ``ansible_host`` to the MagicDNS name.
    """
    labels = hostvars.get("hcloud_labels") or {}
    return isinstance(labels, dict) and labels.get("network") == "private"


def private_hosts(hostvars_by_host: dict[str, dict]) -> list[str]:
    return sorted(
        host
        for host, hostvars in hostvars_by_host.items()
        if is_private_network_host(hostvars)
    )


def tailscale_binary() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    if Path(TAILSCALE_APP_CLI).exists():
        return TAILSCALE_APP_CLI
    return None


def read_status(binary: str) -> dict | None:
    """``tailscale status --json``, or None when the daemon cannot be asked."""
    try:
        result = subprocess.run(
            [binary, "status", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return None


def _tailnet_names(status: dict) -> set[str]:
    """Short device names in the tailnet, as MagicDNS resolves them."""
    names: set[str] = set()
    devices = [status.get("Self") or {}, *(status.get("Peer") or {}).values()]
    for device in devices:
        dns_name = (device.get("DNSName") or "").rstrip(".")
        if dns_name:
            names.add(dns_name.split(".")[0].lower())
    return names


def ensure_tailnet_access(
    project: str,
    hosts: list[str],
    *,
    strict: bool = True,
    binary_finder: Callable[[], str | None] = tailscale_binary,
    status_reader: Callable[[str], dict | None] = read_status,
) -> None:
    """Fail with instructions unless this machine can reach ``hosts``.

    ``strict=False`` only warns about hosts missing from the tailnet — for the
    infrastructure run, which may be the one that is about to bring them up.
    """
    intro = (
        f"{project} runs in private network mode: its servers are only "
        "reachable over Tailscale."
    )
    binary = binary_finder()
    if binary is None:
        raise click.ClickException(
            f"{intro}\nTailscale is not installed on this machine.\n\n{INSTALL_HINT}"
        )

    status = status_reader(binary)
    if status is None:
        raise click.ClickException(
            f"{intro}\nThe Tailscale daemon is not running.\n\n"
            "  macOS app:  open Tailscale\n"
            "  Homebrew:   sudo brew services start tailscale\n"
            "  Linux:      sudo systemctl enable --now tailscaled"
        )

    state = status.get("BackendState")
    if state != "Running":
        raise click.ClickException(
            f"{intro}\nTailscale is installed but not connected (state: {state}).\n\n"
            "Connect with `tailscale up` (or sign in through the app), then retry."
        )

    missing = sorted(set(hosts) - _tailnet_names(status))
    if not missing:
        return

    tailnet = (status.get("CurrentTailnet") or {}).get("Name") or "your tailnet"
    message = (
        f"{intro}\nNot found in {tailnet}: {', '.join(missing)}.\n\n"
        "Either this machine is signed in to a different tailnet "
        "(`tailscale switch --list`), your account has not been invited to the "
        "project's tailnet (ask its owner: admin console → Users → Invite), or "
        "the servers have not joined yet."
    )
    if strict:
        raise click.ClickException(message)
    click.echo(f"Warning: {message}", err=True)
