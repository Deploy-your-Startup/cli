"""``startup --version`` names the installed release, so scripts can require one."""

import importlib.metadata
import subprocess
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src"


def test_version_names_the_installed_release(tmp_path):
    result = subprocess.run(
        [sys.executable, "-m", "cli.startup", "--version"],
        cwd=tmp_path,
        env={"PYTHONPATH": str(SOURCE), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    installed = importlib.metadata.version("deploy-your-startup-cli")
    assert result.stdout.strip() == f"startup {installed}"
