"""Use Ansible's executable interface; never import its Python implementation.

Plaintext travels through stdin/stdout. Passwords travel through inherited pipes,
not arguments, environment additions or files on disk. This supports the CLI's
macOS and Linux runtimes.
"""

import os
import subprocess
import tempfile
from contextlib import ExitStack, contextmanager
from pathlib import Path

from ..ansible_bin import ansible_bin


class VaultError(ValueError):
    """An operation failed; the message is safe to show in diagnostics."""


@contextmanager
def _password_pipe(password):
    encoded = password.encode() if isinstance(password, str) else password
    if not encoded or not encoded.strip():
        raise VaultError("Vault password cannot be empty.")
    # Bound the write to fit an empty pipe before the child starts.
    if len(encoded) > 4095 or b"\n" in encoded or b"\r" in encoded:
        raise VaultError("Vault password must be one line of at most 4095 bytes.")
    reader, writer = os.pipe()
    try:
        try:
            os.write(writer, encoded + b"\n")
        finally:
            os.close(writer)
        yield reader
    finally:
        os.close(reader)


def _run(operation, data, password, *arguments, new_password=None):
    with ExitStack() as stack:
        descriptor = stack.enter_context(_password_pipe(password))
        descriptors = [descriptor]
        command = [
            ansible_bin("ansible-vault"),
            operation,
            "--vault-password-file",
            f"/dev/fd/{descriptor}",
        ]
        if new_password is not None:
            new_descriptor = stack.enter_context(_password_pipe(new_password))
            descriptors.append(new_descriptor)
            command += ["--new-vault-password-file", f"/dev/fd/{new_descriptor}"]
        command.extend(arguments)
        try:
            result = subprocess.run(
                command,
                input=data,
                capture_output=True,
                check=False,
                pass_fds=tuple(descriptors),
            )
        except OSError:
            raise VaultError("Could not start ansible-vault.") from None
        if result.returncode:
            # Ansible's diagnostics can contain input fragments. Do not forward
            # them, or a CalledProcessError carrying argv/input, to callers.
            raise VaultError(
                f"ansible-vault {operation} failed; check the password and Vault input."
            )
        return result.stdout


def decrypt(data, password):
    """Decrypt bytes without stripping significant whitespace."""
    if isinstance(data, str):
        data = data.encode()
    return _run("decrypt", data, password, "--output", "-")


def encrypt(data, password):
    """Encrypt bytes, without persisting plaintext."""
    if isinstance(data, str):
        data = data.encode()
    return _run("encrypt", data, password, "--output", "-")


def encrypt_inline(name, data, password):
    """Generate the YAML scalar via stdin, including option-like secret values."""
    if isinstance(data, str):
        data = data.encode()
    return _run("encrypt_string", data, password, "--stdin-name", name).decode()


def rekey(data, old_password, new_password):
    """Rekey a private ciphertext copy; caller commits only a complete result."""
    if isinstance(data, str):
        data = data.encode()
    with tempfile.TemporaryDirectory(prefix="startup-vault-") as directory:
        path = Path(directory) / "ciphertext"
        path.write_bytes(data)
        path.chmod(0o600)
        _run("rekey", None, old_password, str(path), new_password=new_password)
        return path.read_bytes()
