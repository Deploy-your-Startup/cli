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
