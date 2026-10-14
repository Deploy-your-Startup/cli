"""Exercise doctor output through the real CLI, including a terminal."""

import errno
import os
import pty
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def doctor_environment(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    git = shutil.which("git")
    assert git is not None
    (binaries / "git").symlink_to(git)
    (home / ".gitconfig").write_text(
        "[user]\nname = Doctor Test\nemail = doctor@example.com\n"
    )
    gh = binaries / "gh"
    gh.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$*" >> "$DOCTOR_GH_CALLS"\n'
        '[ "$*" = "auth status" ] || exit 99\n'
        'echo "synthetic-private-token" >&2\n'
        'case "$DOCTOR_GH_STATUS" in\n'
        "  authenticated) echo \"Token scopes: 'workflow', 'read:packages'\" ;;\n"
        "  missing-scopes) echo \"Token scopes: 'repo'\" ;;\n"
        "  denied) exit 1 ;;\n"
        "  *) exit 99 ;;\n"
        "esac\n"
    )
    gh.chmod(0o755)
    environment = {
        **os.environ,
        "HOME": str(home),
        "PATH": str(binaries),
        "PYTHONPATH": str(ROOT / "src"),
        "GIT_CONFIG_GLOBAL": str(home / ".gitconfig"),
        "GIT_CONFIG_NOSYSTEM": "1",
        "DOCTOR_GH_CALLS": str(tmp_path / "gh-calls"),
        "DOCTOR_GH_STATUS": "authenticated",
        "TERM": "xterm-256color",
    }
    for name in ("NO_COLOR", "FORCE_COLOR", "CLICOLOR", "CLICOLOR_FORCE"):
        environment.pop(name, None)
    return environment


@pytest.mark.parametrize("github_status", ["authenticated", "missing-scopes", "denied"])
def test_doctor_reports_passes_and_missing_prerequisites(
    tmp_path, doctor_environment, github_status
):
    # GIVEN real Git, missing local tools/key, and a local GitHub CLI stand-in.
    doctor_environment["DOCTOR_GH_STATUS"] = github_status

    # WHEN the user runs the public doctor command with redirected output.
    result = subprocess.run(
        [sys.executable, "-m", "cli.startup", "doctor", "--provider", "hetzner"],
        env=doctor_environment,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    # THEN successes and failures have readable markers and actionable guidance.
    assert result.returncode == 1, result.stderr
    assert "✓ git executable on PATH" in result.stdout
    assert "✗ Missing: uv executable on PATH" in result.stdout
    assert "✗ Missing: SSH public key for server access" in result.stdout
    login_marker = "✗ Missing:" if github_status == "denied" else "✓"
    assert f"{login_marker} GitHub login (gh auth login" in result.stdout
    if github_status == "missing-scopes":
        assert (
            "✗ Missing: GitHub workflow + package scopes (gh auth refresh"
            in result.stdout
        )
    elif github_status == "authenticated":
        assert "✓ GitHub workflow + package scopes" in result.stdout
    else:
        assert "GitHub workflow + package scopes" not in result.stdout
    assert "Prerequisites are incomplete; no resources were created." in result.stderr
    assert "synthetic-private-token" not in result.stdout + result.stderr
    assert "\x1b[" not in result.stdout
    assert "OK  " not in result.stdout
    assert "MISSING" not in result.stdout
    assert Path(doctor_environment["DOCTOR_GH_CALLS"]).read_text() == "auth status\n"


def test_doctor_uses_green_checks_and_red_crosses_in_a_terminal(
    tmp_path, doctor_environment
):
    # GIVEN the same prerequisite checks in an interactive terminal.
    master, slave = pty.openpty()
    output = b""
    try:
        # WHEN the real doctor command writes to a terminal instead of a pipe.
        result = subprocess.run(
            [sys.executable, "-m", "cli.startup", "doctor", "--provider", "hetzner"],
            env=doctor_environment,
            cwd=tmp_path,
            stdin=subprocess.DEVNULL,
            stdout=slave,
            stderr=slave,
            timeout=30,
            check=False,
        )
    finally:
        os.close(slave)
        try:
            while chunk := os.read(master, 65536):
                output += chunk
        except OSError as error:
            if error.errno != errno.EIO:
                raise
        finally:
            os.close(master)

    # THEN the symbols carry the terminal colors and labels stay readable.
    text = output.decode("utf-8")
    assert result.returncode == 1, text
    assert "\x1b[32m\x1b[1m  ✓ \x1b[0mgit executable on PATH" in text
    assert "\x1b[31m\x1b[1m  ✗ \x1b[0mMissing: uv executable on PATH" in text
    assert "synthetic-private-token" not in text


def test_basic_doctor_needs_no_server_browser_or_cloud_account(
    tmp_path, doctor_environment
):
    # GIVEN GitHub and the CLI runtime, but no SSH key, browser or cloud account.
    binaries = Path(doctor_environment["PATH"])
    (binaries / "uv").write_text("#!/bin/sh\nexit 0\n")
    (binaries / "uv").chmod(0o755)
    # WHEN checking the installation through the actual CLI process.
    result = subprocess.run(
        [sys.executable, "-m", "cli.startup", "doctor"],
        env=doctor_environment,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    # THEN setup succeeds without asking for deployment prerequisites.
    assert result.returncode == 0, result.stdout + result.stderr
    for dependency in ("SSH", "Chrome", "Hetzner", "domain", "macOS"):
        assert dependency not in result.stdout


def test_byos_doctor_accepts_a_custom_key_without_hetzner_or_chrome(
    tmp_path, doctor_environment
):
    # GIVEN a personal key outside the two default filenames.
    ssh_keygen = shutil.which("ssh-keygen")
    assert ssh_keygen is not None
    (Path(doctor_environment["PATH"]) / "ssh-keygen").symlink_to(ssh_keygen)
    public_key = tmp_path / "custom.pub"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(tmp_path / "custom")],
        check=True,
    )
    # WHEN checking BYOS prerequisites using the explicit path.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "doctor",
            "--provider",
            "byos",
            "--ssh-public-key",
            str(public_key),
        ],
        env=doctor_environment,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    # THEN the custom key passes and no browser/Hetzner account is requested.
    assert "✓ SSH public key for server access" in result.stdout
    assert "✗ Missing: SSH public key" not in result.stdout
    assert "Chrome" not in result.stdout
    assert "No Hetzner account is required" in result.stdout


def test_doctor_does_not_report_invalid_keys_as_ready(tmp_path, doctor_environment):
    # GIVEN a file that exists but contains no valid public key.
    key = tmp_path / "invalid.pub"
    key.write_text("ssh-ed25519 malformed-synthetic-key")
    # WHEN checking the selected deployment key with the real CLI.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "doctor",
            "--provider",
            "byos",
            "--ssh-public-key",
            str(key),
        ],
        env=doctor_environment,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    # THEN a missing/invalid key is reported without leaking file contents.
    assert result.returncode != 0
    assert "✗ Missing: SSH public key for server access" in result.stdout
    assert "malformed-synthetic-key" not in result.stdout + result.stderr
