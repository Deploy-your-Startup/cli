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
