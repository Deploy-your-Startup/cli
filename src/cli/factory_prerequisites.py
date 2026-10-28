"""Shared Factory tool requirements for diagnosis and setup."""

import shutil
import subprocess

import click

from cli import wizard_output as ui

# kubectl is optional: only inspecting a full-stack cluster needs it.
FACTORY_TOOLS = ("mise", "uv", "yq", "gh")


def missing_factory_tools() -> list[str]:
    return [tool for tool in FACTORY_TOOLS if not shutil.which(tool)]


def factory_tool_checks() -> list[tuple[bool, str]]:
    return [
        (shutil.which(tool) is not None, f"{tool} executable on PATH")
        for tool in FACTORY_TOOLS
        if tool not in ("uv", "gh")  # Already covered by the basic CLI checks.
    ]


def ensure_factory_tools() -> None:
    missing = missing_factory_tools()
    if missing and shutil.which("brew"):
        ui.info("Installing missing Factory tools with Homebrew: " + ", ".join(missing))
        subprocess.run(["brew", "install", *missing], check=True)
        missing = missing_factory_tools()
    if missing:
        raise click.ClickException(
            "Missing Factory tools: "
            + ", ".join(missing)
            + ". Install with `brew install "
            + " ".join(missing)
            + "`, then rerun this command; existing Factory files are preserved."
        )
