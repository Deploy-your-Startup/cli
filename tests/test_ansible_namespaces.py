import json

from cli import ansible_commands


def test_resolve_k8s_namespace_reads_plain_value_without_parsing_vault(tmp_path):
    deployment = tmp_path / "deployment"
    group_vars = deployment / "group_vars"
    group_vars.mkdir(parents=True)
    (group_vars / "all.yml").write_text(
        'k8s_namespace: "my-shop"\nsecret: !vault |\n  encrypted\n',
        encoding="utf-8",
    )

    assert ansible_commands._resolve_k8s_namespace(deployment) == "my-shop"


def test_configure_kubeconfig_context_sets_project_namespace():
    kubeconfig = {
        "clusters": [{"name": "default", "cluster": {}}],
        "users": [{"name": "default", "user": {}}],
        "contexts": [
            {
                "name": "default",
                "context": {"cluster": "default", "user": "default"},
            }
        ],
        "current-context": "default",
    }

    ansible_commands._configure_kubeconfig_context(
        kubeconfig, "my-shop-production", "my-shop"
    )

    assert kubeconfig["clusters"][0]["name"] == "my-shop-production"
    assert kubeconfig["users"][0]["name"] == "my-shop-production"
    assert kubeconfig["contexts"][0] == {
        "name": "my-shop-production",
        "context": {
            "cluster": "my-shop-production",
            "user": "my-shop-production",
            "namespace": "my-shop",
        },
    }
    assert kubeconfig["current-context"] == "my-shop-production"


def test_byos_backup_leaves_the_namespace_to_group_vars(tmp_path, monkeypatch):
    project_root = tmp_path / "my-shop"
    deployment = project_root / "deployment"
    shared = deployment / ".shared-roles"
    group_vars = deployment / "group_vars"
    shared.mkdir(parents=True)
    group_vars.mkdir()
    (shared / "backup-playbook.yml").write_text("---\n", encoding="utf-8")
    (deployment / ansible_commands.BYOS_INVENTORY).write_text("---\n")
    (group_vars / "all.yml").write_text('k8s_namespace: "my-shop"\n')
    captured = {}

    monkeypatch.setattr(ansible_commands, "setup_ansible", lambda **kwargs: shared)

    def fake_run_byos(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(ansible_commands, "_run_byos_playbook", fake_run_byos)

    ansible_commands.run_backup(
        vault_password="secret",
        environment="production",
        working_directory=str(deployment),
    )

    # --extra-vars outranks every other source, so passing the namespace here
    # would silently win over group_vars. The playbook resolves it itself.
    assert "k8s_namespace" not in captured["extra_vars"]
    assert "project_name=my-shop" in captured["extra_vars"]


def test_byos_restore_leaves_the_namespace_to_group_vars(tmp_path, monkeypatch):
    project_root = tmp_path / "my-shop"
    deployment = project_root / "deployment"
    shared = deployment / ".shared-roles"
    group_vars = deployment / "group_vars"
    backup_root = tmp_path / "backups"
    shared.mkdir(parents=True)
    group_vars.mkdir()
    backup_root.mkdir()
    (shared / "restore-playbook.yml").write_text("---\n", encoding="utf-8")
    (deployment / ansible_commands.BYOS_INVENTORY).write_text("---\n")
    (group_vars / "all.yml").write_text('k8s_namespace: "my-shop"\n')
    db_file = backup_root / "my-shop-db-production-test.sql.gz"
    db_file.write_text("data")
    captured = {}

    monkeypatch.setattr(ansible_commands, "setup_ansible", lambda **kwargs: shared)

    def fake_run_byos(*args, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(ansible_commands, "_run_byos_playbook", fake_run_byos)

    ansible_commands.run_restore(
        vault_password="secret",
        environment="production",
        working_directory=str(deployment),
        backup_dir=str(backup_root),
        restore_media=False,
        confirm=True,
    )

    extra_vars = json.loads(captured["extra_vars"])
    assert "k8s_namespace" not in extra_vars
    assert extra_vars["project_name"] == "my-shop"


def test_resolve_k8s_namespace_ignores_a_nested_key(tmp_path):
    deployment = tmp_path / "deployment"
    group_vars = deployment / "group_vars"
    group_vars.mkdir(parents=True)
    (group_vars / "all.yml").write_text(
        "some_chart_values:\n  k8s_namespace: not-the-project\n",
        encoding="utf-8",
    )

    assert ansible_commands._resolve_k8s_namespace(deployment) == "default"
