"""Exercise `startup lint-contract` through the real CLI on rendered layouts."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

MAKE = """#!/bin/bash
# `uvx` in a comment is documentation, not a call.
set -e
cd "$(dirname "$0")"
if [ "${1:-}" == "format" ]; then
    uv run --group dev ruff format
fi
if [ "${1:-}" == "lint" ]; then
    uv run --group dev ruff format --check
    uv run --group dev ty check
fi
"""
PYPROJECT = """[project]
name = "backend"
version = "0"
dependencies = ["django>=5.2"]

[dependency-groups]
dev = ["ruff>=0.16,<0.18", "ty==0.0.89", "django-stubs>=5.2"]
"""
WORKFLOW = """name: Build and Deploy Backend
on:
  pull_request:
  push:
    branches: [main]
jobs:
  build-and-deploy:
    uses: owner/deploy-your-startup/.github/workflows/build-and-deploy-service-parallel.yml@abc
    with:
      service: backend
      run_lint: true
"""


@pytest.fixture
def project(tmp_path):
    """A Django service that keeps the contract, as the templates render it."""
    backend = tmp_path / "backend"
    backend.mkdir()
    (backend / "make.sh").write_text(MAKE)
    (backend / "pyproject.toml").write_text(PYPROJECT)
    (backend / ".python-version").write_text("3.14\n")
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    (deployment / "make.sh").write_text('#!/bin/bash\nset -euo pipefail\necho "$1"\n')
    (tmp_path / "mise.toml").write_text('[tools]\nnode = "24"\n')
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    (workflows / "build-and-deploy-backend.yml").write_text(WORKFLOW)
    return tmp_path


def lint_contract(project: Path) -> tuple[int, str]:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "lint-contract",
            "--project-dir",
            project,
        ],
        env={**os.environ, "PYTHONPATH": str(ROOT / "src"), "NO_COLOR": "1"},
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    return result.returncode, result.stdout + result.stderr


def replace(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def test_compliant_project_passes_without_changing_files(project):
    # GIVEN a project whose services keep the format/lint contract.
    before = {path: path.read_bytes() for path in project.rglob("*") if path.is_file()}
    # WHEN checking the lint contract.
    code, output = lint_contract(project)
    # THEN it passes, counts both services and leaves every file untouched.
    assert code == 0, output
    assert "kept by 2 service(s)" in output
    assert {
        path: path.read_bytes() for path in project.rglob("*") if path.is_file()
    } == before


def test_make_script_without_errexit_fails(project):
    # GIVEN a make.sh that would mask a failing ruff run behind a passing ty run.
    replace(project / "backend" / "make.sh", "set -e\n", "")
    # WHEN checking the lint contract.
    code, output = lint_contract(project)
    # THEN the missing `set -e` is reported and the command fails.
    assert code == 1
    assert "backend/make.sh: add `set -e`" in output


def test_lint_without_format_and_uvx_calls_fail(project):
    # GIVEN a service that lints with an unpinned uvx tool and cannot format.
    make = project / "backend" / "make.sh"
    replace(make, "uv run --group dev ruff format\n", "echo formatted\n")
    replace(make, '"format" ]', '"fmt" ]')
    replace(make, "uv run --group dev ty check", "uvx ty check")
    # WHEN checking the lint contract.
    code, output = lint_contract(project)
    # THEN both broken promises are reported.
    assert code == 1
    assert "`lint` needs a matching `format` command" in output
    assert "use `uv run` with pinned tools, not `uvx`" in output


def test_case_dispatch_is_recognised(project):
    # GIVEN a make.sh that dispatches commands with a case statement.
    (project / "backend" / "make.sh").write_text(
        '#!/bin/bash\nset -e\ncase "$1" in\n'
        "  format) uv run ruff format ;;\n"
        "  lint|check) uv run ruff check ;;\n"
        "esac\n"
    )
    # WHEN checking the lint contract.
    code, output = lint_contract(project)
    # THEN both commands count as implemented.
    assert code == 0, output


def test_python_runtime_and_tool_pins_are_enforced(project):
    # GIVEN a Django service with a ty range, no stubs, no .python-version and
    # a mise.toml that manages uv and Python itself.
    backend = project / "backend"
    replace(backend / "pyproject.toml", '"ty==0.0.89"', '"ty>=0.0.89"')
    replace(backend / "pyproject.toml", ', "django-stubs>=5.2"', "")
    (backend / ".python-version").unlink()
    (backend / "mise.toml").write_text(
        '[tools]\npython = "3.14"\nuv = "latest"\n\n'
        '[tasks.lint]\nrun = "uvx ruff check"\n'
    )
    # WHEN checking the lint contract.
    code, output = lint_contract(project)
    # THEN each deviation names the file and the fix.
    assert code == 1
    for message in (
        "backend: commit a .python-version",
        "backend/pyproject.toml: pin ty exactly (ty==<version>)",
        "backend/pyproject.toml: Django services need django-stubs",
        "backend/mise.toml: remove `uv` from [tools]",
        "backend/mise.toml: remove `python` from [tools]",
        "backend/mise.toml: task lint uses `uvx`",
    ):
        assert message in output
    assert "6 issue(s)" in output


def test_git_hooks_are_rejected(project):
    # GIVEN a project that adds a pre-commit configuration.
    (project / ".pre-commit-config.yaml").write_text("repos: []\n")
    # WHEN checking the lint contract.
    code, output = lint_contract(project)
    # THEN the hook is reported; checks belong in make.sh lint and CI.
    assert code == 1
    assert ".pre-commit-config.yaml: remove Git hooks" in output


def test_workflow_without_pull_request_or_lint_gate_fails(project):
    # GIVEN a caller workflow that only runs on push and does not enable lint.
    workflow = project / ".github" / "workflows" / "build-and-deploy-backend.yml"
    replace(workflow, "  pull_request:\n", "")
    replace(workflow, "      run_lint: true\n", "")
    # WHEN checking the lint contract.
    code, output = lint_contract(project)
    # THEN the check explains that nothing would block a merge.
    assert code == 1
    assert "add a pull_request trigger" in output
    assert "set run_lint: true so CI gates backend" in output


def test_parallel_services_inherit_and_override_run_lint(project):
    # GIVEN a parallel workflow whose services list adds a frontend without lint
    # and overrides run_lint for it.
    frontend = project / "frontend"
    frontend.mkdir()
    (frontend / "make.sh").write_text('#!/bin/bash\nset -e\necho "$1"\n')
    workflow = project / ".github" / "workflows" / "build-and-deploy-backend.yml"
    replace(
        workflow,
        "      service: backend\n",
        "      service: application\n"
        '      services: \'[{"service":"backend"},'
        '{"service":"frontend","run_lint":true}]\'\n',
    )
    # WHEN checking the lint contract.
    code, output = lint_contract(project)
    # THEN only the frontend, which cannot lint, is reported.
    assert code == 1
    assert "run_lint is true but frontend/make.sh has no `lint`" in output
    assert "1 issue(s)" in output


def test_directory_without_services_is_an_error(tmp_path):
    # GIVEN a directory that is not a project.
    # WHEN checking the lint contract.
    code, output = lint_contract(tmp_path)
    # THEN the command fails instead of reporting an empty pass.
    assert code == 1
    assert "No make.sh found" in output
