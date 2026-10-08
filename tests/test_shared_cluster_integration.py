"""Real CLI processes, Copier projects and static Ansible inventories."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from cli.bootstrap import TEMPLATE_VAULT_PASSWORD, template_replacements
from cli.cluster_commands import configure_project_cluster
from cli.template_commands import render_project
from cli.wizard.context import BootstrapContext

ROOT = Path(__file__).resolve().parents[1]
LOCAL_TEMPLATE = ROOT.parent / "django-backend-template"
TEMPLATE = (
    str(LOCAL_TEMPLATE)
    if LOCAL_TEMPLATE.is_dir()
    else "https://github.com/Deploy-your-Startup/django-backend-template.git"
)
TEMPLATE_VERSION = (
    "HEAD" if LOCAL_TEMPLATE.is_dir() else "8e1a387ad45f0d6c9727d4a52ac4d245e8176790"
)


def launch(*args, cwd, env=None):
    return subprocess.run(
        [sys.executable, "-m", "cli.startup", *args],
        cwd=cwd,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "STARTUP_VAULT_PASSWORD": "integration-only",
            "NO_COLOR": "1",
            **(env or {}),
        },
        input="n\nn\n",
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def descriptor():
    return {
        "schema": 1,
        "id": "a" * 32,
        "owner": "example-owner/cluster-owner",
        "environment": "production",
        "nodes": [
            {
                "name": "cluster-owner-master-0",
                "host": "203.0.113.10",
                "ssh_user": "root",
                "role": "master",
            },
            {
                "name": "cluster-owner-worker-0",
                "host": "203.0.113.11",
                "ssh_user": "root",
                "role": "worker",
            },
        ],
    }


def test_two_rendered_projects_keep_separate_data_and_reuse_cluster_inventory(tmp_path):
    # GIVEN the real application template and an existing cluster descriptor.
    owner: dict = {}
    for name in ("cluster-owner", "second-startup"):
        ctx = BootstrapContext(
            project_name=name,
            base_domain=f"{name}.example.com",
            additional_domains="",
            github_username="example-owner",
            postgres_version="18.6",
            sentry_dsn="",
            output_dir=tmp_path,
            shared_cluster=name == "cluster-owner",
            provider="hetzner" if name == "cluster-owner" else "byos",
            cluster_config=None
            if name == "cluster-owner"
            else {**descriptor(), "id": owner["id"]},
        )
        # WHEN real Copier renders each application and cluster settings are written.
        render_project(
            ctx.project_dir,
            template_replacements(
                project_name=name,
                base_domain=ctx.base_domain,
                additional_domains="",
                github_username="example-owner",
                docker_registry_host="ghcr.io",
                postgres_version="18.6",
                k8s_namespace=name,
                ci_public_key="ssh-ed25519 AAAATEST integration",
                user_public_key="ssh-ed25519 AAAATEST operator",
            ),
            source=str(TEMPLATE),
            version=TEMPLATE_VERSION,
        )
        configure_project_cluster(ctx)
        before = (ctx.deployment_dir / "cluster.yml").read_bytes()
        configure_project_cluster(ctx)
        assert (ctx.deployment_dir / "cluster.yml").read_bytes() == before
        settings = yaml.safe_load(before)
        variables = yaml.load(
            (ctx.deployment_dir / "group_vars/all.yml").read_text(),
            Loader=yaml.BaseLoader,
        )
        if not owner:
            owner = settings
            assert settings["managed"] is True
            assert not (ctx.deployment_dir / "inventory.byos.yml").exists()
        else:
            assert settings["managed"] is False
            # THEN the second startup targets actual owner nodes, without cloud credentials.
            result = subprocess.run(
                [
                    "ansible-inventory",
                    "-i",
                    str(ctx.deployment_dir / "inventory.byos.yml"),
                    "--list",
                    "--vault-password-file",
                    "/bin/cat",
                ],
                input=TEMPLATE_VAULT_PASSWORD,
                capture_output=True,
                text=True,
                check=True,
            )
            inventory = yaml.safe_load(result.stdout)
            assert (
                inventory["_meta"]["hostvars"]["cluster-owner-master-0"][
                    "startup_cluster_id"
                ]
                == owner["id"]
            )
            assert (
                inventory["_meta"]["hostvars"]["cluster-owner-master-0"][
                    "startup_cluster_managed"
                ]
                is False
            )
            assert inventory["hcloud_type_master"]["hosts"] == [
                "cluster-owner-master-0"
            ]
            assert (
                inventory["_meta"]["hostvars"]["cluster-owner-worker-0"]["ansible_host"]
                == "203.0.113.11"
            )
        assert variables["startup_namespace_policy"] == "true"
        assert variables["startup_project_owner"] == ctx.full_repo
        assert (
            f'k8s_namespace: "{name}"'
            in (ctx.deployment_dir / "group_vars/all.yml").read_text()
        )
        assert "ci_ssh_key" not in before.decode()
        assert "token" not in before.decode()


@pytest.mark.parametrize(
    "command", ["infrastructure", "update-vms", "k3s-upgrade", "cert-manager-upgrade"]
)
def test_attached_project_rejects_cluster_operations_before_remote_work(
    tmp_path, command
):
    # GIVEN a real project directory attached to an owner-managed cluster.
    deployment = tmp_path / "deployment"
    deployment.mkdir()
    (deployment / "cluster.yml").write_text(
        yaml.safe_dump({**descriptor(), "managed": False})
    )
    # An external Keychain stand-in records any unauthorized credential lookup.
    executable_dir = tmp_path / "bin"
    executable_dir.mkdir()
    accessed = tmp_path / "credential-accessed"
    security = executable_dir / "security"
    security.write_text(f"#!/bin/sh\ntouch '{accessed}'\nexit 1\n")
    security.chmod(0o755)
    # WHEN its user invokes a cluster-changing CLI command.
    result = launch(
        "ansible",
        command,
        "--working-directory",
        str(deployment),
        "--environment",
        "production",
        cwd=tmp_path,
        env={
            "PATH": str(executable_dir) + os.pathsep + os.environ["PATH"],
            "STARTUP_VAULT_PASSWORD": "",
        },
    )
    assert not accessed.exists()
    # THEN it fails before installing collections, cloning roles or contacting a server.
    assert result.returncode != 0
    assert "run it from example-owner/cluster-owner" in result.stderr
    assert not (deployment / ".shared-roles").exists()


@pytest.mark.parametrize(
    "extra",
    [
        ("--provider", "hetzner"),
        ("--shared-cluster",),
        ("--buy-domain",),
        ("--byos-host", "203.0.113.20"),
    ],
)
def test_attach_conflicts_fail_before_creating_a_project(tmp_path, extra):
    # GIVEN a valid descriptor, WHEN conflicting bootstrap options are supplied.
    path = tmp_path / "cluster.yml"
    path.write_text(yaml.safe_dump(descriptor()))
    result = launch(
        "bootstrap", "--kind", "fullstack", "--cluster", str(path), *extra, cwd=tmp_path
    )
    # THEN no wizard or provider mutation runs.
    assert result.returncode != 0
    assert "--cluster cannot be combined" in result.stderr
    assert list(tmp_path.iterdir()) == [path]


def test_descriptor_rejects_arbitrary_inventory_and_credentials(tmp_path):
    # GIVEN a descriptor containing an extra sensitive/inventory field.
    data = descriptor()
    data["nodes"][0]["ansible_ssh_private_key_file"] = "/private/key"
    path = tmp_path / "cluster.yml"
    path.write_text(yaml.safe_dump(data))
    # WHEN bootstrap reads it, THEN it rejects it before external work.
    result = launch("bootstrap", "--cluster", str(path), cwd=tmp_path)
    assert result.returncode != 0
    assert "Invalid cluster node fields" in result.stderr


def test_attached_launch_plan_cancels_without_provisioning(tmp_path):
    # GIVEN a valid public descriptor, WHEN the user declines the launch plan.
    path = tmp_path / "cluster.yml"
    path.write_text(yaml.safe_dump(descriptor()))
    result = launch(
        "bootstrap",
        "--kind",
        "fullstack",
        "--cluster",
        str(path),
        "--project-name",
        "second-startup",
        "--base-domain",
        "second.example.com",
        "--github-username",
        "example-owner",
        "--output-dir",
        str(tmp_path / "apps"),
        cwd=tmp_path,
    )
    # THEN the owner and application-only mode are clear and no project exists.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "example-owner/cluster-owner" in result.stdout
    assert "application deployment only" in result.stdout
    assert not (tmp_path / "apps").exists()


@pytest.mark.parametrize("provider", ["byos", "hetzner"])
def test_export_uses_real_inventory_and_publishes_only_public_fields(
    tmp_path, provider
):
    # GIVEN an owner project and a real local shared-role Git repository.
    source = tmp_path / "shared-source"
    (source / "roles").mkdir(parents=True)
    (source / "roles/.keep").write_text("")
    subprocess.run(
        ["git", "init", "-b", "main", str(source)], check=True, capture_output=True
    )
    subprocess.run(["git", "add", "."], cwd=source, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Integration",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "fixture",
        ],
        cwd=source,
        check=True,
        capture_output=True,
    )
    deployment = tmp_path / "cluster-owner" / "deployment"
    (deployment / "group_vars").mkdir(parents=True)
    settings = {**descriptor(), "managed": True}
    (deployment / "cluster.yml").write_text(yaml.safe_dump(settings))
    (deployment / "group_vars/all.yml").write_text(
        "project_name: cluster-owner\nsynthetic_secret: never-export-this\n"
    )
    inventory_path = (
        deployment / "inventory.byos.yml"
        if provider == "byos"
        else source / "inventory.hcloud.yml"
    )
    inventory_path.write_text(
        yaml.safe_dump(
            {
                "all": {
                    "children": {
                        "hcloud_type_master": {
                            "hosts": {
                                "cluster-owner-master-0": {
                                    "ansible_host": "203.0.113.10",
                                    "ansible_user": "root",
                                    "hcloud_labels": {"type": "master"},
                                }
                            }
                        },
                    }
                }
            }
        )
    )
    if provider == "hetzner":
        from tests.test_vault_process_integration import encrypt

        (deployment / "hcloud_token_production").write_text(
            encrypt(tmp_path, b"synthetic-cloud-token", password="integration-only")
        )
    output = tmp_path / "connection.yml"
    # WHEN the real CLI sets up shared roles and exports through ansible-inventory.
    result = launch(
        "cluster",
        "export",
        "--working-directory",
        str(deployment),
        "--repo-url",
        str(source),
        "--output",
        str(output),
        cwd=tmp_path,
    )
    # THEN only the schema's public connection fields leave the project.
    assert result.returncode == 0, result.stdout + result.stderr
    exported = yaml.safe_load(output.read_text())
    assert set(exported) == {"schema", "id", "owner", "environment", "nodes"}
    assert exported["nodes"][0]["host"] == "203.0.113.10"
    assert "never-export-this" not in output.read_text() + result.stdout + result.stderr
    before = output.read_bytes()
    retry = launch(
        "cluster",
        "export",
        "--working-directory",
        str(deployment),
        "--repo-url",
        str(source),
        "--output",
        str(output),
        cwd=tmp_path,
    )
    assert retry.returncode != 0
    assert output.read_bytes() == before


def test_local_setup_uses_the_reviewed_workflow_ref_before_pinning(tmp_path):
    # GIVEN real Git branches and a generated project recording a reviewed ref.
    source = tmp_path / "shared-source"
    role = source / "roles/sample/tasks/main.yml"
    role.parent.mkdir(parents=True)
    role.write_text("[]\n")
    subprocess.run(
        ["git", "init", "-b", "main", str(source)], check=True, capture_output=True
    )
    subprocess.run(["git", "add", "."], cwd=source, check=True)
    git_env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Integration",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Integration",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(
        ["git", "commit", "-m", "main"],
        cwd=source,
        env=git_env,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "checkout", "-b", "codex/reviewed"],
        cwd=source,
        check=True,
        capture_output=True,
    )
    role.write_text(
        "- name: Reviewed branch\n  ansible.builtin.debug:\n    msg: reviewed\n"
    )
    subprocess.run(["git", "add", "."], cwd=source, check=True)
    subprocess.run(
        ["git", "commit", "-m", "reviewed"],
        cwd=source,
        env=git_env,
        check=True,
        capture_output=True,
    )
    expected = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source, text=True
    ).strip()
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "clone", "--bare", str(source), str(remote)],
        check=True,
        capture_output=True,
    )
    project = tmp_path / "app"
    deployment = project / "deployment"
    deployment.mkdir(parents=True)
    (project / ".copier-answers.yml").write_text("deploy_ref: codex/reviewed\n")
    # WHEN the real CLI sets up local deployment without a manual --version override.
    result = launch(
        "ansible",
        "setup_ansible",
        "--working-directory",
        str(deployment),
        "--repo-url",
        remote.as_uri(),
        cwd=tmp_path,
    )
    # THEN the local role checkout and pin match the workflow ref, rather than main.
    assert result.returncode == 0, result.stdout + result.stderr
    assert (deployment / "shared-roles.ref").read_text().strip() == expected
    assert (
        "Reviewed branch"
        in (deployment / ".shared-roles/roles/sample/tasks/main.yml").read_text()
    )


@pytest.mark.parametrize("operation", ["backup", "restore"])
def test_data_playbooks_load_project_and_environment_group_vars(tmp_path, operation):
    from cli.ansible_commands import _project_data_playbook, _run_command

    # GIVEN a shared playbook and inventory outside the project's variable root.
    deployment = tmp_path / "deployment"
    shared = deployment / ".shared-roles"
    shared.mkdir(parents=True)
    variables = deployment / "group_vars"
    variables.mkdir()
    (variables / "all.yml").write_text("k8s_namespace: all-startup\n")
    (variables / "production.yml").write_text("k8s_namespace: production-startup\n")
    inventory = shared / "inventory.ini"
    inventory.write_text("[production]\nlocalhost ansible_connection=local\n")
    playbook = shared / f"{operation}-playbook.yml"
    playbook.write_text(
        "- hosts: production\n  gather_facts: false\n  tasks:\n"
        "    - ansible.builtin.assert:\n"
        "        that: k8s_namespace == 'production-startup'\n"
        "    - ansible.builtin.copy:\n"
        f"        dest: '{tmp_path / 'result'}'\n"
        "        content: '{{ k8s_namespace }}'\n"
    )
    # WHEN Ansible executes the real imported playbook through the CLI's wrapper.
    with _project_data_playbook(deployment, playbook) as wrapper:
        result = _run_command(
            [
                str(Path(sys.executable).parent / "ansible-playbook"),
                str(wrapper),
                "-i",
                str(inventory),
            ],
            cwd=deployment,
            capture_output=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    # THEN environment overrides apply and the temporary wrapper is removed.
    assert (tmp_path / "result").read_text() == "production-startup"
    assert not wrapper.exists()
