"""Run real pytest workers and the actual CI gate to verify complete sharding."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def run_pytest(suite, *args):
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-n", "2", *args],
        cwd=suite,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_shards_execute_every_case_exactly_once_and_propagate_failure(tmp_path):
    # GIVEN a real parametrized suite using our actual collection hook.
    shutil.copyfile(ROOT / "tests/conftest.py", tmp_path / "conftest.py")
    (tmp_path / "test_cases.py").write_text(
        """
from pathlib import Path
import pytest

@pytest.mark.parametrize("case", range(13))
def test_case(case):
    Path(f"ran-{case}").write_text("executed")
    assert case != 7, "intentional scenario failure"
"""
    )
    # WHEN each shard executes with two real workers.
    seen = set()
    outcomes = []
    for index in range(1, 4):
        result = run_pytest(tmp_path, "--shard", f"{index}/3")
        outcomes.append(result.returncode)
        executed = {path.name for path in tmp_path.glob("ran-*")}
        # THEN no scenario is duplicated, omitted or turned green by sharding.
        assert executed
        assert not seen & executed
        seen.update(executed)
        for path in tmp_path.glob("ran-*"):
            path.unlink()
    assert seen == {f"ran-{case}" for case in range(13)}
    assert sorted(outcomes) == [0, 0, 1]
    # Without the option, local pytest still executes every scenario.
    unsharded = run_pytest(tmp_path)
    assert unsharded.returncode == 1
    assert {path.name for path in tmp_path.glob("ran-*")} == seen


@pytest.mark.parametrize("shard", ["0/3", "4/3", "1/0", "bad"])
def test_invalid_shards_fail_instead_of_silently_skipping(tmp_path, shard):
    # GIVEN the actual hook and a collectable test.
    shutil.copyfile(ROOT / "tests/conftest.py", tmp_path / "conftest.py")
    (tmp_path / "test_case.py").write_text("def test_case(): pass\n")
    # WHEN a malformed shard is requested, THEN pytest reports a usage error.
    result = run_pytest(tmp_path, "-n", "0", "--shard", shard)
    assert result.returncode == 4, result.stdout + result.stderr
    assert "--shard must be INDEX/TOTAL" in result.stderr


def test_ci_gate_requires_every_matrix_shard():
    # GIVEN the real workflow, including the preserved required check name.
    jobs = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text())["jobs"]
    shard_job = jobs["test-shard"]
    assert shard_job["strategy"] == {"fail-fast": False, "matrix": {"shard": [1, 2, 3]}}
    assert (
        next(step for step in shard_job["steps"] if step.get("name") == "Run tests")[
            "run"
        ]
        == "./make.sh test --shard ${{ matrix.shard }}/3"
    )
    gate = jobs["test"]
    assert gate["if"] == "always()"
    assert gate["needs"] == ["test-shard"]
    step = gate["steps"][0]
    assert step["env"]["SHARD_RESULT"] == "${{ needs.test-shard.result }}"
    # WHEN executing its real shell contract for each possible matrix result,
    # THEN only success permits the required check to pass.
    for status in ("success", "failure", "cancelled", "skipped"):
        result = subprocess.run(
            ["bash", "-e", "-c", step["run"]],
            env={**os.environ, "SHARD_RESULT": status},
            check=False,
        )
        assert (result.returncode == 0) is (status == "success")
