"""Check the suite's ambient CLI guard through real nested pytest processes."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("ambient_call", [False, True])
def test_checkout_cli_works_but_swallowed_ambient_calls_fail(tmp_path, ambient_call):
    # GIVEN the real suite fixture and an independent checkout CLI process.
    shutil.copyfile(Path(__file__).with_name("conftest.py"), tmp_path / "conftest.py")
    (tmp_path / "test_runtime.py").write_text(
        "import subprocess, sys\n"
        "def test_runtime():\n"
        "    result = subprocess.run([sys.executable, '-m', 'cli.startup', '--help'], capture_output=True)\n"
        "    assert result.returncode == 0, result.stderr\n"
        + (
            "    subprocess.run(['startup', '--help'], capture_output=True, check=False)\n"
            if ambient_call
            else ""
        )
    )
    # WHEN the child suite uses the checkout or swallows an ambient failure.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(tmp_path)],
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )
    # THEN only the checkout-only scenario passes; swallowed calls still fail.
    assert (result.returncode == 0) is not ambient_call, result.stdout + result.stderr
    if ambient_call:
        assert "Tests called startup through the ambient PATH" in result.stdout
