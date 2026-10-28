"""Bootstrap location selection through the real command and terminal."""

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
ARGS = [
    sys.executable,
    "-m",
    "cli.startup",
    "bootstrap",
    "--kind",
    "fullstack",
    "--provider",
    "byos",
    "--project-name",
    "example",
    "--base-domain",
    "example.com",
    "--github-username",
    "example-owner",
    "--without-auth",
]


def factory(path):
    (path / "scripts").mkdir(parents=True)
    (path / "scripts/init.sh").write_text("#!/bin/sh\n")
    (path / "projects.yaml").write_text("projects: []\ntooling: []\n")
    return path


def environment(home):
    return {
        **os.environ,
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home / ".config"),
        "PYTHONPATH": str(ROOT / "src"),
        "NO_COLOR": "1",
        "TERM": "xterm",
        "STARTUP_NO_ANIMATION": "1",
    }


def launch(home, cwd, *args, answers="\nn\nn\n"):
    return subprocess.run(
        [*ARGS, *args],
        cwd=cwd,
        env=environment(home),
        input=answers,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize("location", ["inside", "outside", "saved"])
def test_bootstrap_suggests_factory_before_any_setup(tmp_path, location):
    # GIVEN an existing factory, including one created at a custom location.
    target = factory(
        tmp_path / ("custom" if location == "saved" else "Projects") / "my factory"
    )
    cwd = target / "scripts" if location == "inside" else tmp_path
    if location == "saved":
        # Use the public state writer with this isolated application's config path.
        script = (
            "from cli.workspace_state import WorkspaceState\n"
            f"from pathlib import Path\ns = WorkspaceState.start(Path({str(target)!r}), 'local', 'HEAD', {{}})\n"
            "s.data['step'] = 'complete'\ns.save()\n"
        )
        subprocess.run(
            [sys.executable, "-c", script], env=environment(tmp_path), check=True
        )
    # WHEN the actual wizard is run and its launch plan declined.
    result = launch(tmp_path, cwd)
    # THEN the startup belongs to the factory and no project is created.
    assert result.returncode == 0, result.stderr
    assert str(target / "projects/startups/example") in result.stdout
    assert "Cancelled" in result.stdout
    assert not (target / "projects").exists()


def test_explicit_output_and_current_factory_take_precedence(tmp_path):
    # GIVEN two factories, with the command started inside one of them.
    first = factory(tmp_path / "Projects/first")
    factory(tmp_path / "Projects/second")
    # WHEN defaults or an explicit override are used.
    default = launch(tmp_path, first)
    override = launch(tmp_path, first, "--output-dir", "~/Elsewhere", answers="n\nn\n")
    # THEN neither path is ambiguous, and the explicit override wins.
    assert default.returncode == override.returncode == 0
    assert str(first / "projects/startups/example") in default.stdout
    assert str(tmp_path / "Elsewhere/example") in override.stdout


def test_ambiguous_factories_require_selection(tmp_path):
    # GIVEN two factories outside the working directory.
    factory(tmp_path / "Projects/first")
    second = factory(tmp_path / "Projects/second")
    # WHEN unattended mode cannot choose, or the user chooses the second factory.
    unattended = launch(tmp_path, tmp_path, "--yes", answers="")
    selected = launch(tmp_path, tmp_path, answers="2\n\nn\nn\n")
    # THEN unattended setup stops and interactive setup uses the selected factory.
    assert unattended.returncode == 1
    assert "Multiple factories found" in unattended.stderr
    assert selected.returncode == 0, selected.stderr
    assert str(second / "projects/startups/example") in selected.stdout


def test_missing_factory_falls_back_to_projects(tmp_path):
    # GIVEN an ordinary project folder, not a factory.
    (tmp_path / "Projects/ordinary").mkdir(parents=True)
    # WHEN the user inspects the launch plan.
    result = launch(tmp_path, tmp_path)
    # THEN the existing default is retained.
    assert result.returncode == 0, result.stderr
    assert str(tmp_path / "Projects/example") in result.stdout


def test_folder_prompt_rejects_a_file_and_retries(tmp_path):
    # GIVEN a file that cannot contain the new project.
    (tmp_path / "occupied").write_text("keep me")
    # WHEN a user enters that file, then corrects it to a new folder.
    result = launch(tmp_path, tmp_path, answers="occupied\n~/New folder\nn\nn\n")
    # THEN validation explains the problem and the corrected plan is reviewable.
    assert result.returncode == 0, result.stderr
    assert "is a file" in result.stdout
    assert str(tmp_path / "New folder/example") in result.stdout
    assert (tmp_path / "occupied").read_text() == "keep me"


@pytest.mark.parametrize("prefix", ["~/Chosen f", "Chosen f"])
@pytest.mark.parametrize("flow", ["bootstrap", "factory"])
def test_bootstrap_path_completion_in_real_terminal(tmp_path, prefix, flow):
    # GIVEN a real terminal and a directory containing spaces.
    target = tmp_path / "Chosen folder"
    target.mkdir()
    arguments = ARGS
    if flow == "factory":
        (target / "owner.txt").write_text("preserve")
        arguments = [sys.executable, "-m", "cli.startup", "factory", "onboard"]
    master, slave = pty.openpty()
    termios.tcsetwinsize(slave, (40, 240))
    process = subprocess.Popen(
        arguments,
        cwd=tmp_path,
        env=environment(tmp_path),
        stdin=slave,
        stdout=slave,
        stderr=slave,
    )
    os.close(slave)
    steps = [
        (b"Project folder", prefix.encode() + b"\t\n"),
        (b"Configure extra domains", b"n\n"),
        (b"Create this project", b"n\n"),
    ]
    if flow == "factory":
        steps = [
            (b"]: ", prefix.encode() + b"\t\n"),
            (b"Choose another folder", b""),
            (b"]: ", b"\x04"),
        ]
    output = b""
    pending = b""
    deadline = time.monotonic() + 30
    try:
        # WHEN Tab completes the path and the user declines the launch plan.
        while time.monotonic() < deadline:
            if not select.select([master], [], [], 0.1)[0]:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError as exc:
                if exc.errno != errno.EIO:
                    raise
                break
            if not chunk:
                break
            output += chunk
            pending += chunk
            while steps and steps[0][0] in pending:
                marker, answer = steps.pop(0)
                pending = pending.split(marker, 1)[1]
                os.write(master, answer)
        else:
            pytest.fail("Terminal prompt timed out: " + output.decode())
        code = process.wait(timeout=5)
    finally:
        os.close(master)
        if process.poll() is None:
            process.kill()
            process.wait()
    # THEN the real launch plan contains the completed path, including its space.
    assert not steps, output.decode()
    if flow == "factory":
        assert code == 1, output.decode()
        assert "Chosen folder/" in output.decode(), output.decode()
        assert (target / "owner.txt").read_text() == "preserve"
    else:
        assert code == 0, output.decode()
        assert str(target / "example") in output.decode()
    assert not (target / "example").exists()
