"""Real CLI creation with local, committed Copier sources and no account access."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml


def command(cwd: Path, *args: str):
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=True)


@pytest.fixture
def source(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    command(source, "git", "init", "-b", "main")
    for key, value in {
        "user.name": "Template tests",
        "user.email": "test@example.invalid",
        "commit.gpgsign": "false",
        "core.hooksPath": "/dev/null",
    }.items():
        command(source, "git", "config", key, value)
    (source / "copier.yml").write_text("project_name: my-factory\n")
    (source / "README.md.jinja").write_text("# {{ project_name }}\n")
    (source / ".copier-answers.yml.jinja").write_text(
        "{{ _copier_answers | to_nice_yaml }}\n"
    )
    command(source, "git", "add", "-A")
    command(source, "git", "commit", "-m", "baseline")
    return source


def create(source, target, *extra):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "template",
            "create",
            "--template",
            str(source),
            "--version",
            "HEAD",
            "--project-dir",
            str(target),
            *extra,
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
    )


def test_create_factory_from_committed_template(source, tmp_path):
    # GIVEN a committed source and a new workspace with its own name.
    target = tmp_path / "different-name"
    # WHEN the real CLI renders the source without account or infrastructure setup.
    result = create(source, target)
    # THEN the name and durable Copier source are recorded in local files.
    assert result.returncode == 0, result.stdout + result.stderr
    assert (target / "README.md").read_text() == "# different-name\n"
    answers = yaml.safe_load((target / ".copier-answers.yml").read_text())
    assert answers["_src_path"] == str(source)
    assert answers["project_name"] == "different-name"
    assert not (target / ".git").exists()
    assert "created locally" in result.stdout


@pytest.mark.parametrize("existing", ["file", "link"])
def test_create_refuses_owner_files_and_symlinks(source, tmp_path, existing):
    # GIVEN an owner's nonempty workspace, optionally reached through a link.
    owner = tmp_path / "owner"
    owner.mkdir()
    (owner / "notes.md").write_text("keep this")
    target = owner
    if existing == "link":
        target = tmp_path / "alias"
        target.symlink_to(owner, target_is_directory=True)
    # WHEN creation targets it, THEN it refuses without changing owner files.
    result = create(source, target)
    assert result.returncode != 0
    assert "new or empty" in result.stderr
    assert list(owner.iterdir()) == [owner / "notes.md"]
    assert (owner / "notes.md").read_text() == "keep this"


def test_create_rejects_dirty_head_without_creating_destination(source, tmp_path):
    # GIVEN an uncommitted source, WHEN HEAD is selected, THEN no unstable baseline is recorded.
    (source / "README.md.jinja").write_text("uncommitted")
    target = tmp_path / "factory"
    result = create(source, target)
    assert result.returncode != 0
    assert "Commit local template changes" in result.stderr
    assert not target.exists()


def test_create_rejects_nonpublic_data_before_rendering(source, tmp_path):
    # GIVEN an unsupported field, WHEN rendering, THEN it cannot enter the answers.
    target = tmp_path / "factory"
    result = create(source, target, "--data", "password=synthetic-test")
    assert result.returncode != 0
    assert "only public template fields" in result.stderr
    assert not target.exists()
