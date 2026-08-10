import subprocess

import click

from cli import ansible_commands


def test_clone_shared_roles_falls_back_after_git_failure(monkeypatch, tmp_path):
    source = tmp_path / "deploy-template"
    (source / "roles" / "example").mkdir(parents=True)
    (source / "roles" / "example" / "main.yml").write_text("---\n")

    monkeypatch.setattr(
        ansible_commands,
        "_candidate_repo_urls",
        lambda _working_dir, _repo_url=None: [
            "https://github.com/missing/deploy-your-startup.git",
            str(source),
        ],
    )

    def fail_git(*_args, **_kwargs):
        raise click.ClickException("repository not found")

    monkeypatch.setattr(ansible_commands, "_run_command", fail_git)

    target = ansible_commands.clone_or_update_shared_roles(
        working_directory=str(tmp_path),
        shared_dir=".shared-roles",
    )

    assert target == tmp_path / ".shared-roles"
    assert (target / "roles" / "example" / "main.yml").exists()


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True)


def test_stale_sparse_cone_is_widened_even_when_head_matches_remote(tmp_path):
    """A checkout made by an older CLI keeps its narrower sparse cone.

    HEAD then matches the remote, so the up-to-date fast path returns and the
    file that a newer SPARSE_PATHS added is never checked out. `git status` is
    clean throughout — the file is in the commit, just not in the worktree.
    """
    origin = tmp_path / "origin"
    origin.mkdir()
    _git("init", "-q", "-b", "main", cwd=origin)
    _git("config", "user.email", "t@example.com", cwd=origin)
    _git("config", "user.name", "t", cwd=origin)
    (origin / "roles").mkdir()
    (origin / "roles" / "keep.yml").write_text("---\n")
    (origin / "k3s-upgrade-playbook.yml").write_text("---\n")
    _git("add", "-A", cwd=origin)
    _git("commit", "-qm", "init", cwd=origin)

    working_dir = tmp_path / "deployment"
    working_dir.mkdir()
    target = working_dir / ".shared-roles"

    # Simulate the old CLI: clone with a cone that omits the newer root file.
    _git("clone", "-q", str(origin), str(target), cwd=tmp_path)
    _git("config", "core.sparseCheckout", "true", cwd=target)
    _git("sparse-checkout", "init", "--no-cone", cwd=target)
    _git("sparse-checkout", "set", "roles/*", cwd=target)

    assert not (target / "k3s-upgrade-playbook.yml").exists()
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(target),
        check=True,
        capture_output=True,
        text=True,
    )
    assert status.stdout.strip() == "", "the stale cone leaves a clean tree"

    ansible_commands.clone_or_update_shared_roles(
        working_directory=str(working_dir),
        shared_dir=".shared-roles",
        repo_url=str(origin),
        version="main",
    )

    assert (target / "k3s-upgrade-playbook.yml").exists()
    assert (target / "roles" / "keep.yml").exists()
