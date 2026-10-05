from pathlib import Path

import pytest
import yaml
from click import BadParameter
from click.testing import CliRunner

from cli.startup import cli
from cli.template_commands import adopt_project, git, parse_data, render_project
from cli.wizard.context import BootstrapContext
from cli.wizard.steps import project as project_step


def commit(repo: Path, message: str):
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)


def init(repo: Path):
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Template tests")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    git(repo, "config", "tag.gpgsign", "false")
    git(repo, "config", "core.hooksPath", "/dev/null")


@pytest.fixture
def repos(tmp_path):
    template = tmp_path.resolve() / "template"
    init(template)
    (template / "copier.yml").write_text("project_name: demo\n")
    (template / ".copier-answers.yml.jinja").write_text(
        "{{ _copier_answers | to_nice_yaml }}"
    )
    (template / "make.sh").write_text("setup=v1\n\ncustom=template\n")
    (template / "unused.py").write_text("# Intentionally absent in the project\n")
    vault = template / "deployment/group_vars"
    vault.mkdir(parents=True)
    (vault / "production.yml").write_text(
        "base_domain: example.com\nsecret: !vault |\n  $ANSIBLE_VAULT;dummy-template\n"
    )
    commit(template, "baseline")
    git(template, "tag", "v1.0.0")

    project = tmp_path.resolve() / "project"
    init(project)
    git(project, "remote", "add", "origin", "git@github.com:owner/project.git")
    (project / "make.sh").write_text("setup=v1\n\ncustom=project\n")
    (project / "app.py").write_text("# Project business logic\n")
    vault = project / "deployment/group_vars"
    vault.mkdir(parents=True)
    (vault / "production.yml").write_text(
        "base_domain: example.com\nsecret: !vault |\n  $ANSIBLE_VAULT;dummy-project\n"
    )
    commit(project, "existing project")
    return template, project


def test_adopt_preserves_existing_and_deleted_files(repos):
    template, project = repos
    before = {
        p: p.read_bytes()
        for p in project.rglob("*")
        if p.is_file() and ".git" not in p.parts
    }
    adopt_project(project, source=str(template), version="v1.0.0", data={})
    assert git(project, "status", "--porcelain") == "?? .copier-answers.yml"
    assert not (project / "unused.py").exists()
    assert all(p.read_bytes() == content for p, content in before.items())
    answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
    assert answers["_commit"] == "v1.0.0"
    assert "dummy-project" not in (project / ".copier-answers.yml").read_text()


def test_update_preserves_customizations_vault_and_dry_run(repos):
    template, project = repos
    adopt_project(project, source=str(template), version="v1.0.0", data={})
    commit(project, "adopt")
    vault = (project / "deployment/group_vars/production.yml").read_bytes()
    (template / "make.sh").write_text("setup=v2\n\ncustom=template\n")
    (template / "new.py").write_text("# Newly added template file\n")
    (template / "deployment/group_vars/production.yml").write_text(
        "secret: overwritten\n"
    )
    commit(template, "update")
    git(template, "tag", "v2.0.0")
    runner = CliRunner()
    args = ["template", "update", "--project-dir", str(project), "--version", "v2.0.0"]
    preview = runner.invoke(cli, [*args, "--dry-run"])
    assert preview.exit_code == 0, preview.output
    assert "setup=v2" in preview.output
    assert "new.py" in preview.output
    assert not git(project, "status", "--porcelain")
    assert not (project / "new.py").exists()
    result = runner.invoke(cli, args)
    assert result.exit_code == 0, result.output
    assert (project / "make.sh").read_text() == "setup=v2\n\ncustom=project\n"
    assert (project / "deployment/group_vars/production.yml").read_bytes() == vault
    assert not (project / "unused.py").exists()
    assert (project / "new.py").exists()
    commit(project, "update")
    repeat = runner.invoke(cli, args)
    assert repeat.exit_code == 0, repeat.output
    assert not git(project, "status", "--porcelain")


def test_update_reports_conflict_and_preview_preserves_original(repos):
    template, project = repos
    adopt_project(project, source=str(template), version="v1.0.0", data={})
    (project / "make.sh").write_text("setup=project\n\ncustom=project\n")
    commit(project, "adopt and customize")
    (template / "make.sh").write_text("setup=template-v2\n\ncustom=template\n")
    commit(template, "conflicting update")
    git(template, "tag", "v2.0.0")
    args = ["template", "update", "--project-dir", str(project), "--version", "v2.0.0"]
    preview = CliRunner().invoke(cli, [*args, "--dry-run"])
    assert preview.exit_code != 0
    assert "conflicts" in preview.output
    assert not git(project, "status", "--porcelain")
    result = CliRunner().invoke(cli, args)
    assert result.exit_code != 0
    assert "conflicts" in result.output
    assert "<<<<<<<" in (project / "make.sh").read_text()


def test_dirty_project_and_duplicate_adoption_are_rejected(repos):
    template, project = repos
    (project / "app.py").write_text("# Uncommitted work\n")
    args = [
        "template",
        "adopt",
        "--project-dir",
        str(project),
        "--template",
        str(template),
        "--version",
        "v1.0.0",
    ]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code != 0
    assert "local changes" in result.output
    commit(project, "customize")
    assert CliRunner().invoke(cli, args).exit_code == 0
    commit(project, "adopt")
    result = CliRunner().invoke(cli, args)
    assert result.exit_code != 0
    assert "already has" in result.output


def test_secret_fields_are_rejected():
    with pytest.raises(BadParameter, match="public template fields"):
        parse_data(("hcloud_token=secret",))


def test_render_project_uses_existing_placeholder_map(repos, tmp_path):
    template, _ = repos
    target = tmp_path / "generated"
    render_project(
        target,
        {"§§deploy_your_startup.project_name§§": "new-project"},
        source=str(template),
        version="v1.0.0",
    )
    assert (target / "make.sh").exists()
    answers = yaml.safe_load((target / ".copier-answers.yml").read_text())
    assert answers["project_name"] == "new-project"


def test_bootstrap_renders_selected_template_and_resume_preserves_edits(
    repos, tmp_path, monkeypatch
):
    source, _ = repos
    ctx = BootstrapContext(
        project_name="bootstrapped",
        base_domain="example.com",
        additional_domains="",
        github_username="owner",
        postgres_version="17",
        sentry_dsn="",
        output_dir=tmp_path.resolve(),
        provider="byos",
        template_source=str(source),
        template_version="v1.0.0",
    )
    monkeypatch.setattr(
        project_step, "prompt_user_public_key", lambda **_kwargs: "ssh-ed25519 operator"
    )
    monkeypatch.setattr(
        project_step,
        "_generate_ssh_keypair",
        lambda *_args: ("fixture-private", "ssh-ed25519 ci"),
    )
    monkeypatch.setattr(
        project_step, "_generate_docker_config_b64", lambda *_args: "fixture"
    )

    class ReachedVault(Exception):
        pass

    def stop_before_vault(**_kwargs):
        raise ReachedVault

    monkeypatch.setattr("cli.update_vault_secrets.update_secrets", stop_before_vault)
    with pytest.raises(ReachedVault):
        project_step.ProjectStep().run(ctx)
    answers = yaml.safe_load((ctx.project_dir / ".copier-answers.yml").read_text())
    assert answers["project_name"] == "bootstrapped"
    assert answers["_commit"] == "v1.0.0"
    (ctx.project_dir / "make.sh").write_text(
        "# Local edits after interrupted bootstrap\n"
    )
    with pytest.raises(ReachedVault):
        project_step.ProjectStep().run(ctx)
    assert (
        ctx.project_dir / "make.sh"
    ).read_text() == "# Local edits after interrupted bootstrap\n"
