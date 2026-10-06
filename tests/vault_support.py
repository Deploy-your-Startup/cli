"""Independent real-Ansible fixture generation and decryption for integration tests."""

import subprocess
import sys
import tempfile
from pathlib import Path


class RealVault:
    def __init__(self, password):
        self.password = password

    def _run(self, operation, data):
        if isinstance(data, str):
            data = data.encode()
        with tempfile.TemporaryDirectory() as directory:
            password_path = Path(directory) / "synthetic-password"
            password_path.write_text(self.password)
            password_path.chmod(0o600)
            result = subprocess.run(
                [
                    str(Path(sys.executable).parent / "ansible-vault"),
                    operation,
                    "--vault-password-file",
                    str(password_path),
                    "--output",
                    "-",
                ],
                input=data,
                capture_output=True,
                check=True,
                timeout=30,
            )
            return result.stdout

    def encrypt(self, data):
        return self._run("encrypt", data)

    def decrypt(self, data):
        return self._run("decrypt", data)
