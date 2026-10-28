"""Build real test images without requiring VM access to host temporary paths."""

import subprocess
import tarfile
import tempfile
from pathlib import Path


def build_image(image: str, context: Path) -> None:
    # Podman on macOS may not share pytest's temporary directory with its VM.
    # Docker and Podman both accept a complete build-context archive on stdin.
    with tempfile.TemporaryFile() as archive:
        with tarfile.open(fileobj=archive, mode="w") as bundle:
            bundle.add(context, arcname=".")
        archive.seek(0)
        result = subprocess.run(
            ["docker", "build", "-q", "-t", image, "-"],
            stdin=archive,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
    assert result.returncode == 0, result.stdout + result.stderr
