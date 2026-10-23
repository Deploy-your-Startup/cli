"""Kubeconfig files for k3s clusters: naming, contexts and the ~/.kube/config merge.

Pure file and data handling; fetching the file from the master over SSH stays
with ``startup ansible kubeconfig`` in ``ansible_commands``.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml


def extract_master_host(inventory_data: dict) -> str | None:
    for group_name in ("k3s_masters", "masters", "control_plane"):
        hosts = inventory_data.get(group_name, {}).get("hosts", [])
        if hosts:
            return hosts[0]

    for group_name, group_data in inventory_data.items():
        if group_name == "_meta":
            continue
        for host_name in group_data.get("hosts", []) or []:
            if "master" in host_name:
                return host_name
    return None


def derive_context_name(remote_name: str, environment: str, env_suffix: bool) -> str:
    # Nodes are named "<project>-<role>-<index>", e.g. my-app-master-0, so the
    # project is what remains once that suffix is removed. The previous rule
    # kept the first two dash-separated segments instead, which truncated every
    # longer name ("book-reading-club" became "book-reading") and gave two
    # projects sharing their first two segments the very same context.
    stripped = re.sub(r"-(master|worker|agent)-\d+$", "", remote_name.lower())
    project_name = re.sub(r"[^a-z0-9._-]+", "-", stripped)
    context = project_name or f"k3s-{environment}"
    if env_suffix and environment:
        context = f"{context}-{environment}"
    return context


def configure_context(kubeconfig: dict, context: str, namespace: str) -> None:
    """Rename k3s' default identities and select the project's namespace."""
    for item in kubeconfig.get("clusters", []):
        if item.get("name") == "default":
            item["name"] = context
    for item in kubeconfig.get("users", []):
        if item.get("name") == "default":
            item["name"] = context
    for item in kubeconfig.get("contexts", []):
        if item.get("name") == "default":
            item["name"] = context
        context_config = item.setdefault("context", {})
        if context_config.get("cluster") == "default":
            context_config["cluster"] = context
        if context_config.get("user") == "default":
            context_config["user"] = context
        context_config["namespace"] = namespace
    if kubeconfig.get("current-context") == "default":
        kubeconfig["current-context"] = context


def output_path(
    out: str | None, project_name: str, environment: str, byos: bool
) -> Path:
    if out:
        return Path(out).expanduser()
    name = (
        f"k3s-{project_name}-{environment}.yaml" if byos else f"k3s-{environment}.yaml"
    )
    return Path.home() / ".kube" / name


def ssh_options(
    ssh_port: int, identity_options: list[str], port_flag: str
) -> list[str]:
    return [
        port_flag,
        str(ssh_port),
        *identity_options,
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]


def merge_into_kube_config(kubeconfig: dict, context: str, make_current: bool) -> None:
    """Replace this context's entries in ~/.kube/config, keeping all others."""
    from cli.kube_access import access_lock, private_write

    kube_dir = Path.home() / ".kube"
    kube_dir.mkdir(parents=True, exist_ok=True)
    main_config = kube_dir / "config"
    with access_lock(kube_dir / "config.startup.lock"):
        merged: dict = (
            yaml.safe_load(main_config.read_text())
            if main_config.exists()
            else {
                "apiVersion": "v1",
                "kind": "Config",
                "contexts": [],
                "clusters": [],
                "users": [],
            }
        )
        for kind in ("contexts", "clusters", "users"):
            names = {entry["name"] for entry in kubeconfig[kind]}
            merged[kind] = [
                entry for entry in merged.get(kind, []) if entry["name"] not in names
            ] + kubeconfig[kind]
        if make_current or not merged.get("current-context"):
            merged["current-context"] = context
        private_write(main_config, merged)
