"""GIVEN a vault password outside this Mac's Keychain, WHEN the CLI needs it,
THEN it travels through the password manager and never through output or argv.

The OS Keychain is replaced at its boundary: a fake ``security`` executable for
reads and a stand-in ``keyring`` package for writes share one file store.
Ansible and the CLI run for real in a pseudo-terminal.
"""

import errno
import os
import pty
import select
import subprocess
import sys
import textwrap
import time
from pathlib import Path

from tests.vault_support import RealVault

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = "synthetic-handoff-password"
SECRET = b"synthetic-deploy-secret\n"
ENTRY = "VAULT_PASSWORD_DEMO_APP"


def keychain_stand_in(tmp_path):
    """Return (env, store) for a file-backed Keychain and recording clipboard."""
    store = tmp_path / "keychain"
    store.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    security = bin_dir / "security"
    security.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            while [ $# -gt 0 ]; do
              [ "$1" = "-s" ] && service="$2"
              shift
            done
            [ -f "$STARTUP_TEST_KEYCHAIN/$service" ] || exit 44
            cat "$STARTUP_TEST_KEYCHAIN/$service"
            """
        )
    )
    pbcopy = bin_dir / "pbcopy"
    pbcopy.write_text(
        '#!/bin/sh\ncat >> "$STARTUP_TEST_CLIPBOARD"\necho "|" >> "$STARTUP_TEST_CLIPBOARD"\n'
    )
    for tool in (security, pbcopy):
        tool.chmod(0o755)

    stubs = tmp_path / "stubs" / "keyring"
    (stubs / "backends").mkdir(parents=True)
    (stubs / "__init__.py").write_text("")
    (stubs / "errors.py").write_text("class KeyringError(Exception):\n    pass\n")
    (stubs / "backends" / "__init__.py").write_text("")
    (stubs / "backends" / "macOS.py").write_text(
        textwrap.dedent(
            """\
            import os
            from pathlib import Path


            class Keyring:
                def set_password(self, service, username, password):
                    store = Path(os.environ["STARTUP_TEST_KEYCHAIN"])
                    (store / service).write_text(password)
            """
        )
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "PYTHONPATH": f"{tmp_path / 'stubs'}{os.pathsep}{ROOT / 'src'}",
        "STARTUP_TEST_KEYCHAIN": str(store),
        "STARTUP_TEST_CLIPBOARD": str(tmp_path / "clipboard"),
        "STARTUP_NO_ANIMATION": "1",
        "SENTRY_DSN": "",
    }
    env.pop("STARTUP_VAULT_PASSWORD", None)
    env.pop("STARTUP_DISABLE_KEYCHAIN_VAULT", None)
    return env, store


def vaulted_project(tmp_path):
    vault = tmp_path / "demo-app" / "deployment" / "group_vars" / "vault.yml"
    vault.parent.mkdir(parents=True)
    vault.write_bytes(RealVault(PASSWORD).encrypt(SECRET))
    return vault


def run_in_terminal(command, env, cwd, answers):
    """Run ``command`` on a pty, typing each answer once its prompt appears."""
    master, slave = pty.openpty()
    process = subprocess.Popen(
        command, stdin=slave, stdout=slave, stderr=slave, cwd=cwd, env=env
    )
    os.close(slave)
    output = bytearray()
    pending = list(answers)
    deadline = time.monotonic() + 60
    try:
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
                if pending and output.count(pending[0][0]) >= pending[0][2]:
                    os.write(master, pending.pop(0)[1] + b"\n")
            elif process.poll() is not None:
                break
        process.wait(timeout=5)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
    return process.returncode, output.decode("utf-8", errors="replace")


def test_missing_keychain_entry_is_recovered_from_the_password_manager(tmp_path):
    # GIVEN a real vault and a Mac whose Keychain has no entry for the project.
    env, store = keychain_stand_in(tmp_path)
    vault = vaulted_project(tmp_path)
    command = [
        sys.executable,
        "-m",
        "cli.startup",
        "secrets",
        "get-file",
        "-f",
        str(vault),
    ]

    # WHEN the user first pastes a wrong password, then the right one.
    code, text = run_in_terminal(
        command,
        env,
        tmp_path,
        [
            (b"Vault password", b"wrong-synthetic-password", 1),
            (b"Vault password", PASSWORD.encode(), 2),
        ],
    )

    # THEN the wrong one is rejected, the right one opens the vault and is
    # stored under the project's entry, and the password is never echoed.
    assert code == 0, text
    assert ENTRY in text
    assert "does not open this project's vault" in text
    assert SECRET.decode().strip() in text
    assert PASSWORD not in text
    assert (store / ENTRY).read_text() == PASSWORD

    # AND a later run, even without a terminal, reads it from the Keychain.
    later = subprocess.run(
        command, env=env, cwd=tmp_path, capture_output=True, check=False, timeout=60
    )
    assert later.returncode == 0, later.stderr
    assert later.stdout == SECRET


def test_non_interactive_run_fails_with_the_entry_name_instead_of_prompting(tmp_path):
    # GIVEN no Keychain entry and no terminal to ask in.
    env, store = keychain_stand_in(tmp_path)
    vault = vaulted_project(tmp_path)

    # WHEN a command needs the password.
    result = subprocess.run(
        [sys.executable, "-m", "cli.startup", "secrets", "get-file", "-f", str(vault)],
        env=env,
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )

    # THEN it fails at once, names the password manager entry and stores nothing.
    assert result.returncode != 0
    assert ENTRY in result.stderr
    assert result.stdout == ""
    assert list(store.iterdir()) == []


def test_bootstrap_handoff_waits_until_the_password_is_saved(tmp_path):
    # GIVEN a freshly generated vault password during bootstrap.
    env, _ = keychain_stand_in(tmp_path)
    handoff = (
        "from cli.vault_handoff import hand_off_to_password_manager; "
        f"hand_off_to_password_manager('demo-app', {PASSWORD!r})"
    )

    # WHEN the user first declines, then confirms it is saved.
    code, text = run_in_terminal(
        [sys.executable, "-c", handoff],
        env,
        tmp_path,
        [(b"Saved it as", b"n", 1), (b"Saved it as", b"y", 2)],
    )

    # THEN the entry name is shown, the prompt repeats until confirmed, the
    # password went only through the clipboard, and the clipboard is cleared.
    assert code == 0, text
    assert ENTRY in text
    assert text.count("Saved it as") == 2
    assert PASSWORD not in text
    assert (tmp_path / "clipboard").read_text() == f"{PASSWORD}|\n|\n"
