"""Exercise worker isolation through real pytest and Ansible processes."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def test_parallel_workers_use_private_ansible_directories(tmp_path):
    # GIVEN a user collection path and the actual suite fixture in a child suite.
    suite = tmp_path / "suite"
    suite.mkdir()
    shutil.copyfile(Path(__file__).with_name("conftest.py"), suite / "conftest.py")
    user_home = tmp_path / "user-ansible"
    user_home.mkdir()
    sentinel = user_home / "keep"
    sentinel.write_text("user collection")
    (suite / "test_workers.py").write_text(
        """
import json
import os
import subprocess
from pathlib import Path
import pytest

@pytest.mark.parametrize("case", range(4))
def test_worker(case, isolated_ansible_home):
    result = subprocess.run(
        ["ansible-config", "dump", "--format", "json"],
        capture_output=True, text=True, check=True,
    )
    config = {entry["name"]: entry["value"] for entry in json.loads(result.stdout) if "name" in entry}
    home = str(isolated_ansible_home)
    assert config["ANSIBLE_HOME"] == home
    assert config["COLLECTIONS_PATHS"] == [home + "/collections"]
    Path(f"worker-{case}.json").write_text(json.dumps({
        "worker": os.environ["PYTEST_XDIST_WORKER"], "home": home,
    }))
"""
    )
    # WHEN two real pytest workers start with an inherited global collection path.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-n", "2", "-q", str(suite)],
        cwd=suite,
        env={
            **os.environ,
            "ANSIBLE_HOME": str(user_home),
            "ANSIBLE_COLLECTIONS_PATH": str(user_home / "collections"),
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    # THEN Ansible uses a distinct private home in each worker, leaving users alone.
    assert result.returncode == 0, result.stdout + result.stderr
    records = [json.loads(path.read_text()) for path in suite.glob("worker-*.json")]
    assert len(records) == 4
    assert len({record["worker"] for record in records}) == 2
    assert len({record["home"] for record in records}) == 2
    assert all(record["home"] != str(user_home) for record in records)
    assert list(user_home.iterdir()) == [sentinel]
    assert sentinel.read_text() == "user collection"
