"""Real CLI / Ansible interoperability, including lossless inline editing."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src"
ANSIBLE = str(Path(sys.executable).parent / "ansible-vault")
PASSWORD = "integration-vault-password"
NEW_PASSWORD = "integration-new-password"

# Observe real process creation and forbid in-process Ansible imports. Neither
# the CLI nor its child processes are mocked; Ansible starts its own interpreter.
LAUNCH = r"""
import json, os, runpy, sys
class NoAnsible:
    def find_spec(self, fullname, *args):
        if fullname == 'ansible' or fullname.startswith('ansible.'):
            raise AssertionError('CLI imported Ansible: ' + fullname)
sys.meta_path.insert(0, NoAnsible())
def audit(event, args):
    if event == 'subprocess.Popen':
        with open(os.environ['VAULT_TEST_ARGV'], 'a') as stream:
            stream.write(json.dumps(args[1]) + '\n')
sys.addaudithook(audit)
runpy.run_module('cli.startup', run_name='__main__')
"""


def ansible(tmp_path, command, content, password=PASSWORD):
    password_path = tmp_path / "test-password"
    password_path.write_text(password)
    password_path.chmod(0o600)
    try:
        return subprocess.run(
            [
                ANSIBLE,
                command,
                "--vault-password-file",
                str(password_path),
                "--output",
                "-",
            ],
            input=content,
            capture_output=True,
            timeout=30,
            check=False,
        )
    finally:
        password_path.unlink()


def encrypt(tmp_path, content, password=PASSWORD):
    result = ansible(tmp_path, "encrypt", content, password)
    assert result.returncode == 0, result.stderr
    return result.stdout.decode()


def inline(tmp_path, name="secret", value=b"old", indent="", password=PASSWORD):
    cipher = encrypt(tmp_path, value, password)
    return f"{indent}{name}: !vault |\n" + "".join(
        indent + "        " + line + "\n" for line in cipher.splitlines()
    )


def decrypt_field(tmp_path, path, name="secret", password=PASSWORD):
    # Independently load YAML's literal scalar; never use our extraction helper
    # as the oracle for our own replacement implementation.
    import yaml

    class Loader(yaml.SafeLoader):
        pass

    Loader.add_constructor("!vault", lambda loader, node: loader.construct_scalar(node))
    data = yaml.load(path.read_text(), Loader=Loader)
    if "parent" in data:
        data = data["parent"]
    return ansible(tmp_path, "decrypt", data[name].encode(), password)


def cli(tmp_path, *args, stdin=None, password=PASSWORD):
    temporary = tmp_path / "temporary"
    temporary.mkdir(exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-c", LAUNCH, "secrets", *map(str, args)],
        input=stdin,
        capture_output=True,
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(SOURCE),
            "TMPDIR": str(temporary),
            "STARTUP_DISABLE_KEYCHAIN_VAULT": "1",
            "STARTUP_VAULT_PASSWORD": password,
            "VAULT_TEST_ARGV": str(tmp_path / "argv.jsonl"),
        },
        timeout=60,
        check=False,
    )
    assert not list(temporary.iterdir()), "Vault operation left temporary files behind"
    return result


@pytest.mark.parametrize(
    "value",
    [b"--leading-option", "  ünicode\nline two  ".encode(), b"key\n\n", b"single"],
)
def test_inline_update_preserves_neighbours_and_exact_value(tmp_path, value):
    # GIVEN a nested field with deep indentation and comments beside it.
    path = tmp_path / "vars.yml"
    prefix = "# document comment\nparent:\n  before: plain\n"
    suffix = "  # keep this comment\n  after: untouched\nother: value\n"
    path.write_text(prefix + inline(tmp_path, indent="  ") + suffix)
    # WHEN stdin supplies a value (the CLI convention removes one final LF).
    result = cli(
        tmp_path,
        "update",
        "-r",
        path,
        "--field-stdin",
        "secret",
        "--verify-password",
        stdin=value + b"\n",
    )
    # THEN only the block changes, Ansible reads the exact value, and argv is safe.
    assert result.returncode == 0, result.stderr + result.stdout
    text = path.read_text()
    assert text.startswith(prefix)
    assert text.endswith(suffix)
    assert "  secret: !vault |\n    $ANSIBLE_VAULT" in text
    decrypted = decrypt_field(tmp_path, path)
    assert decrypted.returncode == 0, decrypted.stderr
    assert decrypted.stdout == value
    read = cli(tmp_path, "get-field", "-f", path, "--field", "secret", "--verbose")
    assert read.returncode == 0, read.stderr
    assert read.stdout == value + b"\n"
    argv = (tmp_path / "argv.jsonl").read_text()
    assert PASSWORD not in argv
    assert value.decode() not in argv
    assert value not in result.stdout + result.stderr


def test_missing_create_dry_run_and_wrong_password(tmp_path):
    # GIVEN a file with a real block and an unrelated field.
    path = tmp_path / "vars.yml"
    path.write_text(inline(tmp_path) + "plain: keep\n")
    before = path.read_bytes()
    # WHEN a missing field is supplied without explicit creation, it fails.
    missing = cli(
        tmp_path, "update", "-r", path, "--field-stdin", "new", stdin=b"new-value"
    )
    assert missing.returncode != 0
    assert path.read_bytes() == before
    # WHEN creation and replacement are previewed together, both appear in preview.
    preview = cli(
        tmp_path,
        "update",
        "-r",
        path,
        "--field-stdin",
        "new",
        "--field-random",
        "secret",
        "--create-in",
        path,
        "--dry-run",
        stdin=b"new-value",
    )
    assert preview.returncode == 0, preview.stderr + preview.stdout
    assert path.read_bytes() == before
    preview_path = tmp_path / "dry-run-output" / path.name
    assert decrypt_field(tmp_path, preview_path, "new").stdout == b"new-value"
    assert decrypt_field(tmp_path, preview_path).stdout != b"old"
    # WHEN verification fails, even --create-in must not append duplicate fields.
    wrong = cli(
        tmp_path,
        "update",
        "-r",
        path,
        "--field-stdin",
        "secret",
        "--verify-password",
        "--create-in",
        path,
        stdin=b"replacement",
        password="incorrect-test-password",
    )
    assert wrong.returncode != 0
    assert path.read_bytes() == before
    assert b"incorrect-test-password" not in wrong.stdout + wrong.stderr


def test_full_file_read_update_and_rotation(tmp_path):
    # GIVEN a full file with significant whitespace and a separate inline file.
    value = b"  private-key\nsecond line\n\n"
    full = tmp_path / "key.vault"
    full.write_text(encrypt(tmp_path, value))
    fields = tmp_path / "vars.yml"
    fields.write_text(inline(tmp_path, value=value) + "# retain\nplain: yes\n")
    read = cli(tmp_path, "get-file", "-f", full)
    assert read.returncode == 0, read.stderr
    assert read.stdout == value
    # WHEN rotation is previewed and then applied through the real CLI.
    originals = {p: p.read_bytes() for p in (full, fields)}
    args = (
        "rotate-password",
        "-r",
        tmp_path,
        "--old-password",
        PASSWORD,
        "--new-password",
        NEW_PASSWORD,
        "--strict",
    )
    dry = cli(tmp_path, *args, "--dry-run")
    assert dry.returncode == 0, dry.stderr + dry.stdout
    assert all(p.read_bytes() == data for p, data in originals.items())
    rotated = cli(tmp_path, *args)
    assert rotated.returncode == 0, rotated.stderr + rotated.stdout
    # THEN both forms preserve the exact contents and reject the previous key.
    assert ansible(tmp_path, "decrypt", full.read_bytes(), NEW_PASSWORD).stdout == value
    assert ansible(tmp_path, "decrypt", full.read_bytes()).returncode != 0
    assert decrypt_field(tmp_path, fields, password=NEW_PASSWORD).stdout == value
    assert decrypt_field(tmp_path, fields).returncode != 0
    assert fields.read_text().endswith("# retain\nplain: yes\n")


def test_mixed_password_inline_rotation_does_not_partially_write(tmp_path):
    # GIVEN two adjacent blocks with different keys.
    path = tmp_path / "vars.yml"
    path.write_text(
        inline(tmp_path) + inline(tmp_path, name="other", password=NEW_PASSWORD)
    )
    before = path.read_bytes()
    # WHEN a rotation cannot open every block.
    result = cli(
        tmp_path,
        "rotate-password",
        "-r",
        tmp_path,
        "--old-password",
        PASSWORD,
        "--new-password",
        "third-password",
    )
    # THEN failure leaves the whole file intact.
    assert result.returncode != 0
    assert path.read_bytes() == before


def test_duplicate_nested_names_are_all_verified_before_replacement(tmp_path):
    # GIVEN the same field name under two parents, with different passwords.
    path = tmp_path / "vars.yml"
    path.write_text(
        "first:\n"
        + inline(tmp_path, indent="  ")
        + "second:\n"
        + inline(tmp_path, indent="  ", password=NEW_PASSWORD)
    )
    before = path.read_bytes()
    # WHEN replacement requests password verification.
    result = cli(
        tmp_path,
        "update",
        "-r",
        path,
        "--field-stdin",
        "secret",
        "--verify-password",
        stdin=b"replacement",
    )
    # THEN the inaccessible second block prevents any overwrite in this file.
    assert result.returncode != 0
    assert path.read_bytes() == before


def test_full_file_wrong_password_dry_run_fails(tmp_path):
    # GIVEN a full Vault file encrypted with a different key.
    path = tmp_path / "key.vault"
    path.write_text(encrypt(tmp_path, b"untouched", NEW_PASSWORD))
    before = path.read_bytes()
    # WHEN rotation is previewed, the old key must still be checked.
    result = cli(
        tmp_path,
        "rotate-password",
        "-r",
        tmp_path,
        "--old-password",
        PASSWORD,
        "--new-password",
        "next-password",
        "--dry-run",
    )
    # THEN it cannot claim the file is rotatable and leaves it intact.
    assert result.returncode != 0
    assert path.read_bytes() == before


def test_keychain_boundary_and_multiple_files_use_real_ansible(tmp_path):
    # GIVEN the external macOS security boundary replaced by a local executable.
    project = tmp_path / "example"
    deployment = project / "deployment"
    variables = deployment / "group_vars"
    variables.mkdir(parents=True)
    paths = [variables / "all.yml", variables / "production.yml"]
    for path in paths:
        path.write_text(inline(tmp_path) + inline(tmp_path, name="other"))
    binaries = tmp_path / "bin"
    binaries.mkdir()
    security = binaries / "security"
    security.write_text(
        f"#!{sys.executable}\nimport os, sys\n"
        "assert sys.argv[1:] == ['find-generic-password', '-a', os.environ.get('USER', ''), '-s', 'VAULT_PASSWORD_EXAMPLE', '-w']\n"
        "print(os.environ['SYNTHETIC_KEYCHAIN_PASSWORD'])\n"
    )
    security.chmod(0o755)
    env = {
        **os.environ,
        "PYTHONPATH": str(SOURCE),
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "SYNTHETIC_KEYCHAIN_PASSWORD": PASSWORD,
        "VAULT_TEST_ARGV": str(tmp_path / "argv.jsonl"),
    }
    env.pop("STARTUP_VAULT_PASSWORD", None)
    env.pop("STARTUP_DISABLE_KEYCHAIN_VAULT", None)
    # WHEN one real CLI invocation updates multiple fields in multiple files.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            LAUNCH,
            "secrets",
            "update",
            "-r",
            str(deployment),
            "--field-stdin",
            "secret",
            "--field-random",
            "other",
            "--verify-password",
        ],
        input=b"keychain-value",
        env=env,
        cwd=tmp_path,
        capture_output=True,
        timeout=60,
        check=False,
    )
    # THEN scoped Keychain lookup succeeds and Ansible verifies every result.
    assert result.returncode == 0, result.stdout + result.stderr
    for path in paths:
        assert decrypt_field(tmp_path, path).stdout == b"keychain-value"
        other = decrypt_field(tmp_path, path, "other")
        assert other.returncode == 0
        assert len(other.stdout) == 32
    arguments = (tmp_path / "argv.jsonl").read_text()
    assert PASSWORD not in arguments
    assert "keychain-value" not in arguments


@pytest.mark.parametrize("wrong_password", [False, True])
def test_create_in_cannot_corrupt_a_full_vault_file(tmp_path, wrong_password):
    # GIVEN an encrypted YAML file, optionally inaccessible with the supplied key.
    path = tmp_path / "vars.yml"
    path.write_text(encrypt(tmp_path, b"existing: keep\n"))
    before = path.read_bytes()
    # WHEN a missing inline field is requested in that file.
    result = cli(
        tmp_path,
        "update",
        "-r",
        path,
        "--field-stdin",
        "missing",
        "--create-in",
        path,
        stdin=b"new",
        password=NEW_PASSWORD if wrong_password else PASSWORD,
    )
    # THEN the CLI cannot append plaintext YAML to ciphertext.
    assert result.returncode != 0
    assert path.read_bytes() == before


def test_invalid_decrypted_yaml_does_not_leak_into_diagnostics(tmp_path):
    # GIVEN invalid YAML containing a synthetic secret in the parser's error span.
    path = tmp_path / "vars.yml"
    secret = b"private-parser-sentinel"
    path.write_text(encrypt(tmp_path, b"field: [" + secret))
    before = path.read_bytes()
    # WHEN a field update attempts to parse the decrypted document.
    result = cli(tmp_path, "update", "-r", path, "--field-stdin", "field", stdin=b"new")
    # THEN diagnostics contain no decrypted source and the input is preserved.
    assert result.returncode != 0
    assert secret not in result.stdout + result.stderr
    assert path.read_bytes() == before
