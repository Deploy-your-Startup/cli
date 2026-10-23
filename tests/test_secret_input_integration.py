"""Secret values reach the real CLI through stdin or a hidden prompt, never argv.

Every scenario runs `startup secrets` as a child process against real files and
the real `ansible-vault`. The prompt scenarios give the CLI a pseudo-terminal as
its controlling terminal, so the hidden input path is the one a user gets.
"""

import os
import pty
import select
import subprocess
import sys
import time

import pytest

from tests.test_vault_process_integration import (
    LAUNCH,
    PASSWORD,
    SOURCE,
    cli,
    decrypt_field,
    encrypt,
    inline,
)

SECRET = "api-secret-ü\nsecond line  "


def argv_log(tmp_path):
    path = tmp_path / "argv.jsonl"
    return path.read_text() if path.exists() else ""


# After setsid(), the child claims its stdin as controlling terminal: getpass
# opens /dev/tty, so the pseudo-terminal must be that terminal, not the one
# pytest happens to run in.
CLAIM_TERMINAL = "import fcntl, termios; fcntl.ioctl(0, termios.TIOCSCTTY, 0)\n"


def _read_until(master, expected, transcript, start, timeout=30):
    deadline = time.monotonic() + timeout
    while expected not in transcript[start:]:
        remaining = deadline - time.monotonic()
        assert remaining > 0, f"never saw {expected!r} in {transcript!r}"
        ready, _, _ = select.select([master], [], [], remaining)
        if ready:
            try:
                chunk = os.read(master, 1024)
            except OSError:
                chunk = b""
            assert chunk, f"terminal closed before {expected!r}: {transcript!r}"
            transcript += chunk
    return transcript


def cli_in_terminal(tmp_path, *args, answers):
    """Run the CLI on a terminal and type each answer after its prompt appears."""
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, "-c", CLAIM_TERMINAL + LAUNCH, "secrets", *map(str, args)],
        stdin=slave,
        stdout=subprocess.PIPE,
        stderr=slave,
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(SOURCE),
            "STARTUP_DISABLE_KEYCHAIN_VAULT": "1",
            "STARTUP_VAULT_PASSWORD": PASSWORD,
            "VAULT_TEST_ARGV": str(tmp_path / "argv.jsonl"),
        },
        start_new_session=True,
    )
    os.close(slave)
    transcript = b""
    answered = 0
    try:
        for prompt, answer in answers:
            transcript = _read_until(master, prompt, transcript, answered)
            answered = transcript.index(prompt, answered) + len(prompt)
            # Typing only after the prompt guarantees echo is already off, so a
            # secret in the transcript would be a real leak, not a race.
            os.write(master, answer.encode() + b"\n")
        # Keep draining the terminal: getpass restores its settings with
        # TCSAFLUSH, which waits until everything written to it has been read.
        deadline = time.monotonic() + 60
        while process.poll() is None and time.monotonic() < deadline:
            if select.select([master], [], [], 0.1)[0]:
                try:
                    transcript += os.read(master, 1024)
                except OSError:
                    break
        stdout, _ = process.communicate(timeout=60)
    finally:
        if process.poll() is None:
            process.kill()
        os.close(master)
    return process.returncode, stdout, transcript


def test_update_inline_field_reads_an_omitted_value_from_stdin(tmp_path):
    # GIVEN a group_vars file with an existing inline field between plain ones
    path = tmp_path / "vars.yml"
    path.write_text("before: keep\n" + inline(tmp_path, "api_key") + "after: keep\n")

    # WHEN update-inline-field runs without --value and the secret is piped in
    result = cli(
        tmp_path,
        "update-inline-field",
        "-f",
        path,
        "--field",
        "api_key",
        stdin=SECRET.encode() + b"\n",
    )

    # THEN the field decrypts to the exact value, minus only the final newline
    assert result.returncode == 0, result.stderr
    decrypted = decrypt_field(tmp_path, path, "api_key")
    assert decrypted.stdout == SECRET.encode()
    # AND its neighbours survive
    text = path.read_text()
    assert text.startswith("before: keep\napi_key: !vault |\n  $ANSIBLE_VAULT")
    assert text.endswith("after: keep\n")
    # AND the value appears neither in output nor in any child's arguments
    assert b"api-secret" not in result.stdout + result.stderr
    assert "api-secret" not in argv_log(tmp_path)


def test_update_inline_field_creates_a_missing_field(tmp_path):
    # GIVEN a file without the field, and without a final newline
    path = tmp_path / "vars.yml"
    path.write_text("# credentials\nproject_name: example")

    # WHEN the value is passed as - together with --create
    result = cli(
        tmp_path,
        "update-inline-field",
        "-f",
        path,
        "--field",
        "tesla_client_secret",
        "--value",
        "-",
        "--create",
        stdin=SECRET.encode(),
    )

    # THEN an inline !vault block is appended in the two-space layout
    assert result.returncode == 0, result.stderr
    text = path.read_text()
    assert text.startswith(
        "# credentials\nproject_name: example\n\n"
        "tesla_client_secret: !vault |\n  $ANSIBLE_VAULT;1.1;AES256\n"
    )
    # AND real Ansible decrypts it to the exact value
    assert (
        decrypt_field(tmp_path, path, "tesla_client_secret").stdout == SECRET.encode()
    )
    assert "api-secret" not in argv_log(tmp_path)

    # WHEN the same command runs again
    again = cli(
        tmp_path,
        "update-inline-field",
        "-f",
        path,
        "--field",
        "tesla_client_secret",
        "--create",
        stdin=b"rotated",
    )

    # THEN the block is replaced, not appended a second time
    assert again.returncode == 0, again.stderr
    assert path.read_text().count("tesla_client_secret:") == 1
    assert decrypt_field(tmp_path, path, "tesla_client_secret").stdout == b"rotated"


def test_update_inline_field_without_create_leaves_a_missing_field_alone(tmp_path):
    # GIVEN a file without the field
    path = tmp_path / "vars.yml"
    path.write_text("project_name: example\n")

    # WHEN a value is piped in without --create
    result = cli(
        tmp_path,
        "update-inline-field",
        "-f",
        path,
        "--field",
        "new_key",
        stdin=b"value",
    )

    # THEN the command fails, explains --create, and writes nothing
    assert result.returncode == 1
    assert b"--create" in result.stderr
    assert path.read_text() == "project_name: example\n"


def test_missing_field_fails_before_the_terminal_prompt(tmp_path):
    # GIVEN a file without the field and a user at a terminal
    path = tmp_path / "vars.yml"
    path.write_text("project_name: example\n")

    # WHEN update-inline-field runs without --create
    code, _stdout, transcript = cli_in_terminal(
        tmp_path, "update-inline-field", "-f", path, "--field", "new_key", answers=[]
    )

    # THEN it fails without asking for a value that it could not store
    assert code == 1
    assert b"--create" in transcript
    assert b"Value for" not in transcript
    assert path.read_text() == "project_name: example\n"


def test_update_inline_field_refuses_to_append_to_a_full_vault_file(tmp_path):
    # GIVEN a fully encrypted file
    path = tmp_path / "secrets.yml"
    path.write_text(encrypt(tmp_path, b"whole: file\n"))
    before = path.read_bytes()

    # WHEN a field is created in it
    result = cli(
        tmp_path,
        "update-inline-field",
        "-f",
        path,
        "--field",
        "new_key",
        "--create",
        stdin=b"value",
    )

    # THEN it fails without corrupting the file
    assert result.returncode == 1
    assert path.read_bytes() == before


def test_update_inline_field_rejects_empty_stdin(tmp_path):
    # GIVEN an existing field
    path = tmp_path / "vars.yml"
    path.write_text(inline(tmp_path, "api_key"))
    before = path.read_bytes()

    # WHEN nothing arrives on stdin
    result = cli(tmp_path, "update-inline-field", "-f", path, "--field", "api_key")

    # THEN the field keeps its value instead of becoming empty
    assert result.returncode != 0
    assert b"stdin was empty" in result.stderr
    assert path.read_bytes() == before


def test_field_set_dash_reads_stdin_and_creates_the_field(tmp_path):
    # GIVEN a file with one existing field
    path = tmp_path / "vars.yml"
    path.write_text(inline(tmp_path, "existing") + "plain: keep\n")

    # WHEN --field-set uses - as its value, with --create-in for a new field
    result = cli(
        tmp_path,
        "update",
        "-r",
        path,
        "--field-set",
        "cowboy_password",
        "-",
        "--create-in",
        path,
        stdin=SECRET.encode() + b"\n",
    )

    # THEN the new field holds the exact piped value and the rest is unchanged
    assert result.returncode == 0, result.stderr + result.stdout
    assert decrypt_field(tmp_path, path, "cowboy_password").stdout == SECRET.encode()
    assert decrypt_field(tmp_path, path, "existing").stdout == b"old"
    assert "plain: keep\n" in path.read_text()
    assert b"api-secret" not in result.stdout + result.stderr
    assert "api-secret" not in argv_log(tmp_path)


@pytest.mark.parametrize(
    "inputs",
    [
        ["--field-set", "a", "-", "--field-set", "b", "-"],
        ["--field-set", "a", "-", "--field-stdin", "b"],
    ],
)
def test_piped_stdin_feeds_only_one_field(tmp_path, inputs):
    # GIVEN two existing fields
    path = tmp_path / "vars.yml"
    path.write_text(inline(tmp_path, "a") + inline(tmp_path, "b"))
    before = path.read_bytes()

    # WHEN two fields ask for the single piped stdin
    result = cli(tmp_path, "update", "-r", path, *inputs, stdin=b"value")

    # THEN the command is rejected before anything is written
    assert result.returncode == 2
    assert b"stdin holds one value" in result.stderr
    assert path.read_bytes() == before


def test_update_inline_field_prompts_with_hidden_input_in_a_terminal(tmp_path):
    # GIVEN a file without the field and a user at a terminal
    path = tmp_path / "vars.yml"
    path.write_text("project_name: example\n")

    # WHEN update-inline-field runs without --value and the user types it twice
    code, stdout, transcript = cli_in_terminal(
        tmp_path,
        "update-inline-field",
        "-f",
        path,
        "--field",
        "tesla_refresh_token",
        "--create",
        answers=[
            (b"Value for tesla_refresh_token:", "typed-token"),
            (b"Repeat for confirmation:", "typed-token"),
        ],
    )

    # THEN the field is created with the typed value
    assert code == 0, transcript
    assert decrypt_field(tmp_path, path, "tesla_refresh_token").stdout == b"typed-token"
    # AND the value was never echoed, printed or passed as an argument
    assert b"typed-token" not in transcript + stdout
    assert "typed-token" not in argv_log(tmp_path)


def test_field_set_dash_prompts_for_each_field_in_a_terminal(tmp_path):
    # GIVEN one existing field and one that does not exist yet
    path = tmp_path / "vars.yml"
    path.write_text(inline(tmp_path, "cowboy_email"))

    # WHEN both are set with - at a terminal
    code, stdout, transcript = cli_in_terminal(
        tmp_path,
        "update",
        "-r",
        path,
        "--field-set",
        "cowboy_email",
        "-",
        "--field-set",
        "cowboy_password",
        "-",
        "--create-in",
        path,
        answers=[
            (b"Value for cowboy_email:", "rider@example.com"),
            (b"Repeat for confirmation:", "rider@example.com"),
            (b"Value for cowboy_password:", "typed-password"),
            (b"Repeat for confirmation:", "typed-password"),
        ],
    )

    # THEN each field holds the value typed for it
    assert code == 0, transcript + stdout
    assert decrypt_field(tmp_path, path, "cowboy_email").stdout == b"rider@example.com"
    assert decrypt_field(tmp_path, path, "cowboy_password").stdout == b"typed-password"
    # AND neither value leaked
    for value in ("rider@example.com", "typed-password"):
        assert value.encode() not in transcript + stdout
        assert value not in argv_log(tmp_path)
