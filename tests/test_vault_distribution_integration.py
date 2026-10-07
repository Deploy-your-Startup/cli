"""Inspect the actual distribution, including its dependency/license boundary."""

import subprocess
import zipfile
from email.parser import BytesParser
from pathlib import Path


def test_built_wheel_keeps_ansible_separate_and_includes_license_notices(tmp_path):
    # GIVEN the real packaging configuration, WHEN a release wheel is built.
    repo = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    (wheel,) = tmp_path.glob("*.whl")
    # THEN it contains our code and notices, with Ansible only as a dependency.
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "cli/vault/process.py" in names
        assert not any(
            name.startswith(("ansible/", "ansible_collections/")) for name in names
        )
        metadata_name = next(
            name for name in names if name.endswith(".dist-info/METADATA")
        )
        metadata = BytesParser().parsebytes(archive.read(metadata_name))
        assert metadata["License-Expression"] == "MIT"
        assert any(
            dep.startswith("ansible==") for dep in metadata.get_all("Requires-Dist", [])
        )
        assert set(metadata.get_all("License-File", [])) == {
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
        }
        notices = next(
            name for name in names if name.endswith("/licenses/THIRD_PARTY_NOTICES.md")
        )
        assert b"GPL-3.0-or-later" in archive.read(notices)
