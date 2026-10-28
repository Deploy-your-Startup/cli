"""Exercise doctor output through the real CLI, including a terminal."""

import os
import pty
import shutil
import subprocess
import sys
from pathlib import Path
from threading import Event, Thread

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_project_policy_and_server_checks_are_separate_profiles(tmp_path):
    # GIVEN a project directory and two different doctor profiles.
    # WHEN combining project checks with a deployment provider.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "doctor",
            "--project-dir",
            str(tmp_path),
            "--provider",
            "byos",
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )
    # THEN neither profile is silently ignored or confused with installation.
    assert result.returncode == 2
    assert "Use --project-dir separately" in result.stderr


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
    chunks = []
    finished = Event()
    os.set_blocking(master, False)

    def drain():
        while True:
            try:
                if chunk := os.read(master, 65536):
                    chunks.append(chunk)
                    continue
            except BlockingIOError:
                pass
            if finished.is_set():
                return
            finished.wait(0.01)

    # Drain concurrently: macOS PTYs can fill before Doctor finishes writing.
    reader = Thread(target=drain)
    reader.start()
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
        # Keep the slave open until all output is read; macOS may discard it.
        finished.set()
        reader.join(timeout=5)
        assert not reader.is_alive()
        os.close(slave)
        os.close(master)

    # THEN the symbols carry the terminal colors and labels stay readable.
    text = b"".join(chunks).decode("utf-8")
    assert result.returncode == 1, text
    assert "\x1b[32m\x1b[1m  ✓ \x1b[0mgit executable on PATH" in text
    assert "\x1b[31m\x1b[1m  ✗ \x1b[0mMissing: uv executable on PATH" in text
    assert "synthetic-private-token" not in text


def test_basic_doctor_needs_no_server_browser_or_cloud_account(
    tmp_path, doctor_environment
):
    # GIVEN GitHub and the CLI runtime, but no SSH key, browser or cloud account.
    binaries = Path(doctor_environment["PATH"])
    for tool in ("uv", "mise", "yq"):
        (binaries / tool).write_text("#!/bin/sh\nexit 0\n")
        (binaries / tool).chmod(0o755)
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


@pytest.mark.parametrize("installed", [False, True])
def test_doctor_checks_factory_tools_without_installing(
    tmp_path, doctor_environment, installed
):
    # GIVEN a working CLI/GitHub setup and missing or available workspace tools.
    binaries = Path(doctor_environment["PATH"])
    for tool in ("uv", "brew", *(("mise", "yq") if installed else ())):
        path = binaries / tool
        path.write_text("#!/bin/sh\nexit 99\n")
        path.chmod(0o755)
    brew_calls = tmp_path / "brew-calls"
    (binaries / "brew").write_text(
        f"#!{sys.executable}\nfrom pathlib import Path\n"
        f"Path({str(brew_calls)!r}).write_text('unexpected install')\n"
    )
    # WHEN checking the machine through the real doctor command.
    result = subprocess.run(
        [sys.executable, "-m", "cli.startup", "doctor"],
        env=doctor_environment,
        cwd=tmp_path,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    # THEN missing tools fail with a repair command, without executing Homebrew.
    assert result.returncode == (0 if installed else 1), result.stdout + result.stderr
    assert not brew_calls.exists()
    marker = "✓" if installed else "✗ Missing:"
    for tool in ("mise", "yq"):
        assert f"{marker} {tool} executable on PATH" in result.stdout
        assert (binaries / tool).exists() == installed
    if not installed:
        assert "brew install mise yq" in result.stdout


def _doctor_browser_probe(environment, cwd):
    return subprocess.run(
        [sys.executable, "-m", "cli.startup", "doctor", "--provider", "hetzner"],
        env=environment,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


@pytest.mark.parametrize("installed", [True, False])
def test_doctor_checks_version_matched_chromium_without_launching(
    tmp_path, doctor_environment, installed
):
    # GIVEN a separate browser cache and the real installed Playwright driver.
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        installed_executable = Path(playwright.chromium.executable_path)
    assert installed_executable.is_file(), "Install full Chromium before running tests"
    cache = tmp_path / "browser cache"
    cache.mkdir()
    doctor_environment["PLAYWRIGHT_BROWSERS_PATH"] = str(cache)
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            "from playwright.sync_api import sync_playwright; "
            "p = sync_playwright().start(); print(p.chromium.executable_path); p.stop()",
        ],
        env=doctor_environment,
        text=True,
        capture_output=True,
        check=True,
        timeout=30,
    )
    expected = Path(probe.stdout.strip())
    assert expected.is_relative_to(cache)
    if installed:
        expected.parent.mkdir(parents=True)
        expected.symlink_to(installed_executable)
    else:
        # A different revision must not satisfy this installation's requirement.
        (cache / "chromium-old-revision").mkdir()
    before = sorted(cache.rglob("*"))
    # WHEN doctor checks the browser without a download or browser launch.
    result = _doctor_browser_probe(doctor_environment, tmp_path)
    # THEN only the matching executable passes, and the cache stays unchanged.
    marker = "✓" if installed else "✗ Missing:"
    assert f"{marker} Playwright Chromium for browser setup" in result.stdout
    if not installed:
        import shlex

        assert (
            f"{shlex.quote(sys.executable)} -m playwright install chromium"
            in result.stdout
        )
    assert sorted(cache.rglob("*")) == before


def test_doctor_reports_missing_playwright_and_keeps_other_profiles_independent(
    tmp_path, doctor_environment
):
    # GIVEN a real isolated interpreter with CLI dependencies except Playwright.
    import site
    import venv

    isolated = tmp_path / "without-playwright"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(isolated)
    python = isolated / "bin" / "python"
    packages = next((isolated / "lib").glob("python*/site-packages"))
    for source in map(Path, site.getsitepackages()):
        for entry in source.iterdir():
            if (
                entry.name.startswith(("playwright", "__editable__"))
                or entry.suffix == ".pth"
            ):
                continue
            target = packages / entry.name
            if not target.exists():
                target.symlink_to(entry, target_is_directory=entry.is_dir())
    doctor_environment["PLAYWRIGHT_BROWSERS_PATH"] = str(tmp_path / "empty-cache")
    # WHEN checking the Hetzner, basic and BYOS profiles via the actual CLI.
    for arguments in (["--provider", "hetzner"], [], ["--provider", "byos"]):
        result = subprocess.run(
            [str(python), "-m", "cli.startup", "doctor", *arguments],
            env=doctor_environment,
            cwd=tmp_path,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
        # THEN missing Playwright has an actionable repair, only where needed.
        assert "Traceback" not in result.stderr, result.stderr
        if arguments == ["--provider", "hetzner"]:
            assert "Missing: Browser automation requires Playwright" in result.stdout
            assert "Reinstall deploy-your-startup-cli" in result.stdout
            assert "-m playwright install chromium" in result.stdout
        else:
            assert "Playwright" not in result.stdout + result.stderr
            assert "Chromium" not in result.stdout + result.stderr


def test_doctor_rejects_invalid_browser_selection(tmp_path, doctor_environment):
    # GIVEN a misspelled browser selection.
    doctor_environment["STARTUP_BROWSER"] = "chorme"
    # WHEN checking the selected browser through the public CLI.
    result = _doctor_browser_probe(doctor_environment, tmp_path)
    # THEN it fails with the accepted choices, before any browser opens.
    assert result.returncode == 1
    assert "STARTUP_BROWSER must be chromium or chrome" in result.stderr


def test_doctor_checks_selected_chrome_without_requiring_chromium(
    tmp_path, doctor_environment
):
    # GIVEN selected system Chrome and an empty managed-browser cache.
    doctor_environment["STARTUP_BROWSER"] = "chrome"
    doctor_environment["PLAYWRIGHT_BROWSERS_PATH"] = str(tmp_path / "empty-cache")
    # WHEN checking prerequisites through the public CLI.
    result = _doctor_browser_probe(doctor_environment, tmp_path)
    # THEN doctor checks Chrome, without recommending a Chromium download.
    assert "Google Chrome for browser setup" in result.stdout
    assert "playwright install chromium" not in result.stdout
    assert "Playwright Chromium for browser setup" not in result.stdout
    assert not (tmp_path / "empty-cache").exists()
