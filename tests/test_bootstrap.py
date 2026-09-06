from types import SimpleNamespace

from click.testing import CliRunner

from cli import bootstrap
from cli.startup import cli
from cli.sync_commands import _replace_placeholders
from cli.wizard.base import has_placeholders


def test_ensure_ghcr_scopes_accepts_write_packages(monkeypatch):
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(
            stdout="Token scopes: 'repo', 'write:packages'", stderr=""
        )

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)

    bootstrap._ensure_ghcr_scopes()

    assert calls == [["gh", "auth", "status"], ["gh", "auth", "status"]]


def test_template_replacements_include_shared_deploy_repo_name():
    replacements = bootstrap.template_replacements(
        project_name="my-shop",
        base_domain="my-shop.example.com",
        additional_domains="www.my-shop.example.com, api.my-shop.example.com",
        github_username="philipp-lein",
        docker_registry_host="ghcr.io",
        postgres_version="17",
        k8s_namespace="my-shop",
        ci_public_key="ci-public-key",
        user_public_key="user-public-key",
    )

    assert (
        replacements["§§deploy_your_startup.deploy_repo_name§§"]
        == "deploy-your-startup"
    )
    assert replacements["§§deploy_your_startup.k8s_namespace§§"] == "my-shop"


def test_bootstrap_renders_reusable_workflow_references(tmp_path):
    """The bootstrap replacement map must render all caller workflows fully."""
    workflow_dir = tmp_path / ".github" / "workflows"
    workflow_dir.mkdir(parents=True)
    workflows = {
        "build-and-deploy-backend.yml": "build-and-deploy-service.yml",
        "deploy.yml": "deploy.yml",
        "deploy-infrastructure.yml": "deploy-infrastructure.yml",
    }
    for filename, reusable_workflow in workflows.items():
        (workflow_dir / filename).write_text(
            "jobs:\n"
            "  deploy:\n"
            "    uses: §§deploy_your_startup.github_username§§/"
            "§§deploy_your_startup.deploy_repo_name§§/.github/workflows/"
            f"{reusable_workflow}@main\n"
        )

    _replace_placeholders(
        tmp_path,
        bootstrap.template_replacements(
            project_name="my-shop",
            base_domain="my-shop.example.com",
            additional_domains="",
            github_username="philipp-lein",
            docker_registry_host="ghcr.io",
            postgres_version="17",
            k8s_namespace="my-shop",
            ci_public_key="ci-public-key",
            user_public_key="user-public-key",
        ),
    )

    assert has_placeholders(tmp_path) is False
    for filename, reusable_workflow in workflows.items():
        assert (workflow_dir / filename).read_text() == (
            "jobs:\n"
            "  deploy:\n"
            "    uses: philipp-lein/deploy-your-startup/.github/workflows/"
            f"{reusable_workflow}@main\n"
        )


def test_bootstrap_rejects_project_names_that_are_invalid_namespaces():
    runner = CliRunner()

    for project_name in ("my-shop-", "a" * 64):
        result = runner.invoke(
            cli,
            [
                "bootstrap",
                "--yes",
                "--kind",
                "fullstack",
                "--provider",
                "byos",
                "--project-name",
                project_name,
            ],
        )

        assert result.exit_code != 0
        assert "at most 63 characters" in result.output


def test_bootstrap_makes_a_relative_output_dir_absolute(tmp_path, monkeypatch):
    """The wizard's git steps run with cwd=output_dir and pass project_dir as the
    destination. A relative output_dir would therefore be applied twice and the
    template would land in <output_dir>/<output_dir>/<name>."""
    captured = {}

    monkeypatch.setattr(
        "cli.bootstrap_wizard.run_wizard", lambda ctx: captured.update(ctx=ctx)
    )
    monkeypatch.chdir(tmp_path)
    (tmp_path / "projects" / "startups").mkdir(parents=True)

    result = CliRunner().invoke(
        cli,
        [
            "bootstrap",
            "--yes",
            "--kind",
            "fullstack",
            "--provider",
            "byos",
            "--project-name",
            "my-shop-2",
            "--base-domain",
            "my-shop-2.example.com",
            "--github-username",
            "philipp-lein",
            "--byos-host",
            "203.0.113.10",
            "--output-dir",
            "projects/startups",
        ],
    )

    assert result.exit_code == 0, result.output
    ctx = captured["ctx"]
    assert ctx.output_dir.is_absolute()
    assert ctx.project_dir == tmp_path / "projects" / "startups" / "my-shop-2"


def _byos_ctx(tmp_path):
    from cli.bootstrap_wizard import BootstrapContext

    return BootstrapContext(
        project_name="my-shop-2",
        base_domain="my-shop-2.example.com",
        additional_domains="",
        github_username="philipp-lein",
        postgres_version="17",
        sentry_dsn="",
        output_dir=tmp_path,
        provider="byos",
        byos_host="203.0.113.10",
        byos_ssh_user="root",
    )


def test_install_byos_deploy_key_sends_the_key_on_stdin(tmp_path, monkeypatch):
    from cli.wizard.steps import project as project_step

    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs.get("input")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(project_step.subprocess, "run", fake_run)

    installed = project_step.install_byos_deploy_key(
        _byos_ctx(tmp_path), "ssh-ed25519 AAAA my-shop-2_ci\n"
    )

    assert installed is True
    assert captured["command"][0] == "ssh"
    assert "root@203.0.113.10" in captured["command"]
    assert "BatchMode=yes" in captured["command"]
    # The key goes over stdin, never into the remote argv, so nothing has to be
    # quoted and it stays out of the server's process list.
    assert captured["input"] == "ssh-ed25519 AAAA my-shop-2_ci"
    assert not any("AAAA" in part for part in captured["command"])
    # Appending twice would grow authorized_keys on every wizard re-run.
    assert "grep -qxF" in captured["command"][-1]


def test_install_byos_deploy_key_reports_an_unreachable_host(tmp_path, monkeypatch):
    from cli.wizard.steps import project as project_step

    monkeypatch.setattr(
        project_step.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=255),
    )

    assert (
        project_step.install_byos_deploy_key(_byos_ctx(tmp_path), "ssh-ed25519 AAAA")
        is False
    )
