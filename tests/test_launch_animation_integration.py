"""Run onboarding in a real pseudo-terminal; never create external resources."""

import os
import pty
import select
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def terminal_launch(tmp_path, *, static=False, interrupt=False):
    master, slave = pty.openpty()
    environment = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "TERM": "xterm-256color",
        "COLUMNS": "80",
    }
    for name in ("NO_COLOR", "CI", "STARTUP_NO_ANIMATION"):
        environment.pop(name, None)
    if static:
        environment["STARTUP_NO_ANIMATION"] = "1"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "bootstrap",
            "--kind",
            "fullstack",
            "--provider",
            "hetzner",
            "--domain-owned",
            "--github-username",
            "example-owner",
            "--output-dir",
            str(tmp_path / "projects"),
        ],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=environment,
        cwd=tmp_path,
    )
    os.close(slave)
    output = b""
    answered = False
    deadline = time.monotonic() + 10
    try:
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError:
                    break
                if not chunk:
                    break
                output += chunk
                if interrupt and b"\x1b[?25l" in output and not answered:
                    process.send_signal(2)
                    answered = True
                elif b"Project name" in output and not answered:
                    os.write(master, b"launch-check\nexample.com\n\n\n")
                    answered = True
            elif process.poll() is not None:
                break
        process.wait(timeout=3)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
    return process.returncode, output.decode("utf-8"), tmp_path / "projects"


def test_rocket_flies_once_and_stops_before_questions(tmp_path):
    # GIVEN an interactive terminal, WHEN onboarding starts,
    code, output, project = terminal_launch(tmp_path)
    # THEN frames stop before the first prompt, the cursor returns, and cancel is safe.
    assert code == 0, output
    intro, questions = output.split("Project name", 1)
    assert intro.count("\x1b[2K") > 5
    assert intro.count("\x1b[?25l") == 1
    assert intro.count("\x1b[?25h") == 1
    assert "deploy your startup" in intro
    assert "\x1b[2K" not in questions
    assert "Cancelled. No resources were created." in questions
    assert not project.exists()


def test_animation_can_be_disabled(tmp_path):
    # GIVEN the explicit motion opt-out, WHEN onboarding starts in a terminal,
    code, output, project = terminal_launch(tmp_path, static=True)
    # THEN show one stationary brand without animation controls.
    assert code == 0, output
    assert "deploy your startup" in output
    assert "\x1b[2K" not in output
    assert "\x1b[?25l" not in output
    assert not project.exists()


def test_interrupt_restores_cursor(tmp_path):
    # GIVEN a launch in progress, WHEN interrupted,
    code, output, project = terminal_launch(tmp_path, interrupt=True)
    # THEN restore the terminal cursor and do not begin project creation.
    assert code != 0
    assert "\x1b[?25h" in output
    assert "Project name" not in output
    assert not project.exists()
