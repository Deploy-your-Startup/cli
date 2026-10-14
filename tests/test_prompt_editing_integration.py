"""Edit the startup name through a real terminal, without creating resources."""

import errno
import os
import pty
import select
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("terminal_erase", [b"\x08", b"\x7f"])
@pytest.mark.parametrize(
    "typed",
    [
        pytest.param(b"demo-bad\x08\x08\x08good", id="backspace"),
        pytest.param(b"demo-bad\x7f\x7f\x7fgood", id="delete-backward"),
        pytest.param(b"wrong-name\x15demo-good", id="clear-line"),
        pytest.param(b"demo-xgood" + b"\x1b[D" * 4 + b"\x7f", id="cursor-edit"),
        pytest.param(
            b"demo-bad" + b"\x1b[D" * 3 + b"\x1b[3~" * 3 + b"good",
            id="delete-forward",
        ),
    ],
)
def test_startup_name_can_be_edited_before_submission(tmp_path, terminal_erase, typed):
    # GIVEN an actual terminal, including both common erase-key configurations.
    master, slave = pty.openpty()
    settings = termios.tcgetattr(slave)
    settings[6][termios.VERASE] = terminal_erase
    termios.tcsetattr(slave, termios.TCSANOW, settings)
    home = tmp_path / "home"
    home.mkdir()
    inputrc = home / ".inputrc"
    inputrc.write_text("")
    project = tmp_path / "projects"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "bootstrap",
            "--kind",
            "fullstack",
            "--provider",
            "byos",
            "--base-domain",
            "example.com",
            "--github-username",
            "example-owner",
            "--output-dir",
            str(project),
        ],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        cwd=tmp_path,
        env={
            **os.environ,
            "HOME": str(home),
            "INPUTRC": str(inputrc),
            "PYTHONPATH": str(ROOT / "src"),
            "TERM": "xterm-256color",
            "STARTUP_NO_ANIMATION": "1",
            "SENTRY_DSN": "",
        },
    )
    os.close(slave)
    output = bytearray()
    answered = False
    deadline = time.monotonic() + 10
    try:
        # WHEN correcting a typed name before Enter, then declining creation.
        while time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    chunk = os.read(master, 65536)
                except OSError as error:
                    if error.errno == errno.EIO:
                        break
                    raise
                if not chunk:
                    break
                output.extend(chunk)
                if b"Project name" in output and not answered:
                    os.write(master, typed + b"\nn\nn\nn\n")
                    answered = True
            elif process.poll() is not None:
                break
        process.wait(timeout=3)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)

    # THEN the launch plan uses the edited value, without a spurious validation
    # retry or any project creation. This exercises keystrokes, not input strings.
    text = output.decode("utf-8")
    assert process.returncode == 0, text
    assert "example-owner/demo-good" in text, text
    assert "Use lowercase letters" not in text
    assert "Cancelled. No resources were created." in text
    assert not project.exists()
