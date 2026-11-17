"""Keep project-locked CLI execution compatible with its deployment format."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import click


def require_project_runtime(working_dir: Path) -> None:
    """Probe the actual locked runtime before removing its legacy pin files.

    A newer global CLI does not upgrade the CLI installed by deployment/make.sh.
    Probe an isolated interpreter so the caller's checkout/PYTHONPATH cannot
    accidentally make an older installed package appear compatible.
    """
    if not all(
        (working_dir / name).is_file() for name in ("pyproject.toml", "uv.lock")
    ):
        return
    from cli.ansible_commands import _find_uv

    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "VIRTUAL_ENV"}
    }
    result = subprocess.run(
        [
            _find_uv(),
            "run",
            "--locked",
            "--project",
            str(working_dir),
            "python",
            "-I",
            "-c",
            "import importlib.util; raise SystemExit(importlib.util.find_spec('cli.project_version') is None)",
        ],
        cwd=working_dir,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise click.ClickException(
            "The project's locked Startup CLI cannot use the single deployment version. "
            "Update deployment/pyproject.toml and uv.lock to the current CLI release, "
            "then retry startup update. Deployment configuration was not changed."
        )
