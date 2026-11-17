"""ansible-vault children that lost their password pipe are repeated; others are not.

GIVEN a stand-in ``ansible-vault`` placed where the CLI resolves it (next to
its interpreter), which first fails the way Ansible does when the inherited
password pipe is gone and then hands over to the real ``ansible-vault`` with
the same arguments and pipes, WHEN the CLI encrypts and decrypts, THEN those
failures are repeated transparently, while a wrong password or any other
failure ends after a single attempt.
"""

import os
import subprocess
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "src"
REAL_VAULT = Path(sys.executable).parent / "ansible-vault"
PASSWORD = "integration-vault-password"

STAND_IN = """#!/bin/sh
count=$(cat "$VAULT_CALLS" 2>/dev/null || echo 0)
count=$((count + 1))
echo "$count" > "$VAULT_CALLS"
pipe="$3"  # ansible-vault <operation> --vault-password-file /dev/fd/N
if [ "$count" -le "$VAULT_CRASHES" ]; then
  if [ "$VAULT_FAILURE" = lost ] && [ $((count % 2)) -eq 1 ]; then
    echo "[ERROR]: Unexpected Exception, this is probably a bug: [Errno 9] Bad file descriptor: '$pipe'" >&2
    exit 250
  elif [ "$VAULT_FAILURE" = lost ]; then
    echo "[ERROR]: The vault password file $pipe was not found" >&2
    exit 1
  fi
  echo "[ERROR]: Unexpected Exception, this is probably a bug: something else" >&2
  exit 250
fi
exec "{real}" "$@"
"""

ROUND_TRIP = """
import sys
from cli.vault.process import VaultError, decrypt, encrypt
ciphertext = encrypt(b"retry-value", sys.argv[1])
try:
    print(decrypt(ciphertext, sys.argv[2]).decode())
except VaultError as error:
    print(error)
"""


def interpreter(tmp_path):
    """A venv view whose bin/ holds the stand-in next to the real interpreter."""
    real_prefix = Path(sys.prefix)
    root = tmp_path / "venv"
    (root / "bin").mkdir(parents=True)
    (root / "pyvenv.cfg").write_text((real_prefix / "pyvenv.cfg").read_text())
    (root / "lib").symlink_to(real_prefix / "lib")
    python = root / "bin" / "python"
    python.symlink_to(Path(sys.executable).resolve())
    stand_in = root / "bin" / "ansible-vault"
    stand_in.write_text(STAND_IN.format(real=REAL_VAULT))
    stand_in.chmod(0o755)
    return python


def run(tmp_path, crashes, decrypt_password, failure="lost"):
    calls = tmp_path / "calls"
    calls.unlink(missing_ok=True)
    result = subprocess.run(
        [str(interpreter(tmp_path)), "-c", ROUND_TRIP, PASSWORD, decrypt_password],
        env={
            **os.environ,
            "PYTHONPATH": str(SOURCE),
            "VAULT_CALLS": str(calls),
            "VAULT_CRASHES": str(crashes),
            "VAULT_FAILURE": failure,
        },
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    return result.stdout.strip(), int(calls.read_text())


def test_lost_password_pipe_is_repeated(tmp_path):
    output, calls = run(tmp_path, crashes=2, decrypt_password=PASSWORD)
    assert output == "retry-value"
    # EBADF, then "not found", then the successful encrypt and one decrypt.
    assert calls == 4


def test_wrong_password_fails_after_one_attempt(tmp_path):
    output, calls = run(tmp_path, crashes=0, decrypt_password="wrong-password")
    assert output == "ansible-vault decrypt failed; check the password and Vault input."
    assert calls == 2


def test_other_unexpected_failure_is_not_repeated(tmp_path):
    calls_file = tmp_path / "calls"
    result = subprocess.run(
        [str(interpreter(tmp_path)), "-c", ROUND_TRIP, PASSWORD, PASSWORD],
        env={
            **os.environ,
            "PYTHONPATH": str(SOURCE),
            "VAULT_CALLS": str(calls_file),
            "VAULT_CRASHES": "1",
            "VAULT_FAILURE": "other",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode != 0
    assert "ansible-vault encrypt failed" in result.stderr
    assert int(calls_file.read_text()) == 1


def test_persistent_lost_pipe_still_fails(tmp_path):
    calls_file = tmp_path / "calls"
    python = interpreter(tmp_path)
    result = subprocess.run(
        [str(python), "-c", ROUND_TRIP, PASSWORD, PASSWORD],
        env={
            **os.environ,
            "PYTHONPATH": str(SOURCE),
            "VAULT_CALLS": str(calls_file),
            "VAULT_CRASHES": "99",
            "VAULT_FAILURE": "lost",
        },
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode != 0
    assert "ansible-vault encrypt failed" in result.stderr
    assert int(calls_file.read_text()) == 3
