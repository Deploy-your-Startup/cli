from cli.wizard.context import BootstrapContext
from cli.wizard.steps import project as project_step
from cli.wizard.steps.project import BYOS_DISABLED_WORKFLOWS
from cli.wizard.steps.project import BYOS_DEPLOY_PUBLIC_KEY_IGNORE
from cli.wizard.steps.project import BYOS_DEPLOY_PUBLIC_KEY_FILE
from cli.wizard.steps.project import ProjectStep


def test_project_step_skip_restores_vault_password(monkeypatch, tmp_path):
    project_dir = tmp_path / "my-test"
    deployment_dir = project_dir / "deployment"
    deployment_dir.mkdir(parents=True)

    ctx = BootstrapContext(
        project_name="my-test",
        base_domain="example.com",
        additional_domains="",
        github_username="philipp-lein",
        postgres_version="17",
        sentry_dsn="",
        output_dir=tmp_path,
        provider="byos",
    )

    monkeypatch.setattr(project_step, "has_placeholders", lambda _path: False)
    monkeypatch.setattr(
        project_step, "read_keychain_password", lambda _project_name: "stored-secret"
    )
    monkeypatch.setattr(
        project_step, "vault_is_decryptable", lambda _path, password: password == "stored-secret"
    )
    monkeypatch.setattr(project_step.ui, "skip_indicator", lambda _message: None)

    assert ProjectStep().check(ctx) is True
    assert ctx.vault_password == "stored-secret"


def test_disable_byos_ci_workflows_removes_deploy_workflows(tmp_path):
    workflows_dir = tmp_path / ".github" / "workflows"
    workflows_dir.mkdir(parents=True)
    for workflow_name in BYOS_DISABLED_WORKFLOWS:
        (workflows_dir / workflow_name).write_text("name: deploy\n")
    keep = workflows_dir / "dependabot.yml"
    keep.write_text("name: keep\n")

    removed = project_step.disable_byos_ci_workflows(tmp_path)

    assert removed == BYOS_DISABLED_WORKFLOWS
    for workflow_name in BYOS_DISABLED_WORKFLOWS:
        assert not (workflows_dir / workflow_name).exists()
    assert keep.exists()


def test_write_byos_deploy_public_key(tmp_path):
    public_key = "ssh-ed25519 AAAA test@example"

    key_path = project_step.write_byos_deploy_public_key(tmp_path, public_key)

    assert key_path == tmp_path / BYOS_DEPLOY_PUBLIC_KEY_FILE
    assert key_path.read_text() == public_key + "\n"


def test_ensure_byos_deploy_public_key_ignored_appends_once(tmp_path):
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(".idea/\n")

    project_step.ensure_byos_deploy_public_key_ignored(tmp_path)
    project_step.ensure_byos_deploy_public_key_ignored(tmp_path)

    lines = gitignore.read_text().splitlines()
    assert lines.count(BYOS_DEPLOY_PUBLIC_KEY_IGNORE) == 1


def test_byos_deploy_key_install_command(tmp_path):
    ctx = BootstrapContext(
        project_name="my-test",
        base_domain="example.com",
        additional_domains="",
        github_username="philipp-lein",
        postgres_version="17",
        sentry_dsn="",
        output_dir=tmp_path,
        provider="byos",
        byos_host="203.0.113.10",
        byos_ssh_user="root",
    )

    command = project_step.byos_deploy_key_install_command(
        ctx, tmp_path / "deployment" / BYOS_DEPLOY_PUBLIC_KEY_FILE
    )

    assert "ssh root@203.0.113.10" in command
    assert "authorized_keys" in command
    assert "deployment/byos_deploy_key.pub" in command
