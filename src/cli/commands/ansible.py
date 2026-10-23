"""``startup ansible``: run the shared Ansible playbooks for a project.

The commands resolve options and the vault password; ``cli.ansible_commands``
runs the playbooks.
"""

from pathlib import Path

import click


@click.group()
def ansible():
    """Shared Ansible deployment operations"""


@ansible.command("pin")
@click.option("--working-directory", default=".", show_default=True)
@click.option(
    "--version",
    default="main",
    show_default=True,
    help="Shared repository ref to resolve and pin",
)
@click.option("--repo-url", default=None, help="Override the owner shared repository")
def ansible_pin(working_directory, version, repo_url):
    """Pin shared roles to an immutable commit and checksum without deployment."""
    from cli.ansible_commands import run_pin

    run_pin(working_directory=working_directory, version=version, repo_url=repo_url)


@ansible.command("validate")
@click.option("--playbook", required=True, help="Local playbook to syntax-check")
@click.option(
    "--inventory",
    required=True,
    help="Inventory file; use a static test inventory for offline checks",
)
@click.option("--working-directory", default=".", show_default=True)
@click.option("--roles-path", default=None, help="Directory containing local roles")
def ansible_validate(playbook, inventory, working_directory, roles_path):
    """Validate playbook syntax without deploying or refreshing shared roles."""
    from cli.ansible_commands import run_validate

    run_validate(
        playbook=playbook,
        inventory=inventory,
        working_directory=working_directory,
        roles_path=roles_path,
    )


@ansible.command("setup_ansible")
@click.option(
    "--working-directory",
    "--working_directory",
    "working_directory",
    default=".",
    show_default=True,
)
@click.option(
    "--shared-dir", "--shared_dir", default=".shared-roles", show_default=True
)
@click.option("--version", default="main", show_default=True)
@click.option(
    "--refresh/--no-refresh",
    default=True,
    show_default=True,
    help="Refresh `.shared-roles` from git instead of reusing an existing exported copy",
)
@click.option(
    "--repo-url",
    "--repo_url",
    default=None,
    help="Override shared roles repository URL",
)
def ansible_setup_ansible(working_directory, shared_dir, version, refresh, repo_url):
    """Prepare shared roles and install Ansible collections."""
    from cli.ansible_commands import setup_ansible

    setup_ansible(
        working_directory=working_directory,
        shared_dir=shared_dir,
        version=version,
        refresh=refresh,
        repo_url=repo_url,
    )


@ansible.command("setup")
@click.option(
    "--working-directory",
    "--working_directory",
    "working_directory",
    default=".",
    show_default=True,
)
@click.option(
    "--shared-dir", "--shared_dir", default=".shared-roles", show_default=True
)
@click.option("--version", default="main", show_default=True)
@click.option(
    "--refresh/--no-refresh",
    default=True,
    show_default=True,
    help="Refresh `.shared-roles` from git instead of reusing an existing exported copy",
)
@click.option(
    "--repo-url",
    "--repo_url",
    default=None,
    help="Override shared roles repository URL",
)
def ansible_setup(working_directory, shared_dir, version, refresh, repo_url):
    """Install deployment dependencies and prepare shared Ansible roles."""
    from cli.ansible_commands import setup

    setup(
        working_directory=working_directory,
        shared_dir=shared_dir,
        version=version,
        refresh=refresh,
        repo_url=repo_url,
    )


@ansible.command("deploy")
@click.option(
    "--vault-password",
    "--vault_password",
    "vault_password",
    help="Vault password",
)
@click.option("--environment", required=True, help="Target environment")
@click.option(
    "--service", default="all", show_default=True, help="Service tag to deploy"
)
@click.option(
    "--working-directory",
    "--working_directory",
    "working_directory",
    default=".",
    show_default=True,
)
@click.option(
    "--shared-dir", "--shared_dir", default=".shared-roles", show_default=True
)
@click.option("--version", default="main", show_default=True)
@click.option(
    "--refresh/--no-refresh",
    default=True,
    show_default=True,
    help="Refresh `.shared-roles` from git instead of reusing an existing exported copy",
)
@click.option(
    "--repo-url",
    "--repo_url",
    default=None,
    help="Override shared roles repository URL",
)
def ansible_deploy(
    vault_password,
    environment,
    service,
    working_directory,
    shared_dir,
    version,
    refresh,
    repo_url,
):
    """Deploy services via Ansible playbook."""
    from cli.ansible_commands import resolve_vault_password, run_deploy

    resolved_vault_password = resolve_vault_password(
        vault_password=vault_password,
        working_directory=working_directory,
    )

    run_deploy(
        vault_password=resolved_vault_password,
        environment=environment,
        service=service,
        working_directory=working_directory,
        shared_dir=shared_dir,
        version=version,
        refresh=refresh,
        repo_url=repo_url,
    )


@ansible.command("infrastructure")
@click.option(
    "--vault-password",
    "--vault_password",
    "vault_password",
    help="Vault password",
)
@click.option("--environment", required=True, help="Target environment")
@click.option(
    "--working-directory",
    "--working_directory",
    "working_directory",
    default=".",
    show_default=True,
)
@click.option(
    "--shared-dir", "--shared_dir", default=".shared-roles", show_default=True
)
@click.option("--version", default="main", show_default=True)
@click.option(
    "--refresh/--no-refresh",
    default=True,
    show_default=True,
    help="Refresh `.shared-roles` from git instead of reusing an existing exported copy",
)
@click.option(
    "--repo-url",
    "--repo_url",
    default=None,
    help="Override shared roles repository URL",
)
@click.option(
    "--allow-worker-teardown",
    is_flag=True,
    help="Allow deleting surplus workers after a successful drain (this run only)",
)
@click.option(
    "--yes", is_flag=True, help="Confirm an explicitly requested worker teardown"
)
def ansible_infrastructure(
    vault_password,
    environment,
    working_directory,
    shared_dir,
    version,
    refresh,
    repo_url,
    allow_worker_teardown,
    yes,
):
    """Provision infrastructure via Ansible playbook."""
    from cli.ansible_commands import resolve_vault_password, run_infrastructure
    from cli.cluster_commands import require_cluster_owner

    require_cluster_owner(
        Path(working_directory).expanduser().resolve(), "infrastructure"
    )

    if allow_worker_teardown and not yes:
        click.confirm(
            "This infrastructure run may delete surplus worker servers and their local data. Continue?",
            abort=True,
        )

    resolved_vault_password = resolve_vault_password(
        vault_password=vault_password,
        working_directory=working_directory,
    )

    run_infrastructure(
        vault_password=resolved_vault_password,
        environment=environment,
        working_directory=working_directory,
        shared_dir=shared_dir,
        version=version,
        refresh=refresh,
        repo_url=repo_url,
        allow_worker_teardown=allow_worker_teardown,
    )


@ansible.command("firewall")
@click.option(
    "--inspect",
    is_flag=True,
    help="Read live firewall rules and attached server IDs without changing anything.",
)
@click.option("--version", default="main", show_default=True)
@click.option(
    "--repo-url",
    default=None,
    help="Override the shared repository; existing immutable pins still apply.",
)
@click.option("--refresh/--no-refresh", default=True)
@click.option("--working-directory", default="deployment", show_default=True)
@click.option("--environment", type=click.Choice(["production"]), default="production")
@click.option(
    "--dry-run/--execute",
    default=True,
    show_default=True,
    help="Preview or apply only the project's pinned Hetzner firewall role.",
)
def ansible_firewall(
    working_directory, environment, dry_run, repo_url, refresh, version, inspect
):
    """Reconcile firewall rules without provisioning or upgrading servers."""
    from cli.ansible_commands import run_firewall

    run_firewall(
        working_directory,
        environment,
        dry_run=dry_run,
        repo_url=repo_url,
        refresh=refresh,
        version=version,
        inspect=inspect,
    )


@ansible.command("kubeconfig")
@click.option(
    "--ssh-port", type=click.IntRange(1, 65535), default=22, show_default=True
)
@click.option(
    "--direct",
    is_flag=True,
    help="Use direct API access instead of an automatic SSH tunnel.",
)
@click.option(
    "--ssh-identity",
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    help="Persistent SSH identity for automatic tunnels; otherwise use your SSH agent/config.",
)
@click.option(
    "--vault-password",
    "--vault_password",
    "vault_password",
    help="Vault password",
)
@click.option("--environment", required=True, help="Target environment")
@click.option(
    "--working-directory",
    "--working_directory",
    "working_directory",
    default=".",
    show_default=True,
)
@click.option("--inventory", default="inventory.hcloud.yml", show_default=True)
@click.option("--out", default=None, help="Output path for kubeconfig")
@click.option("--ssh-user", "--ssh_user", default="root", show_default=True)
@click.option(
    "--master-host", "--master_host", default=None, help="Override detected master host"
)
@click.option(
    "--context-name",
    "--context_name",
    default=None,
    help="Override derived kube context name",
)
@click.option("--env-suffix/--no-env-suffix", default=True, show_default=True)
@click.option("--make-current/--no-make-current", default=True, show_default=True)
@click.option(
    "--shared-dir", "--shared_dir", default=".shared-roles", show_default=True
)
@click.option("--version", default="main", show_default=True)
@click.option(
    "--refresh/--no-refresh",
    default=True,
    show_default=True,
    help="Refresh `.shared-roles` from git instead of reusing an existing exported copy",
)
@click.option(
    "--repo-url",
    "--repo_url",
    default=None,
    help="Override shared roles repository URL",
)
def ansible_kubeconfig(
    vault_password,
    environment,
    working_directory,
    inventory,
    out,
    ssh_user,
    master_host,
    context_name,
    env_suffix,
    make_current,
    shared_dir,
    version,
    refresh,
    repo_url,
    direct,
    ssh_identity,
    ssh_port,
):
    """Fetch kubeconfig with automatic SSH access (public API access is unnecessary)."""
    from cli.ansible_commands import resolve_vault_password, run_kubeconfig

    resolved_vault_password = resolve_vault_password(
        vault_password=vault_password,
        working_directory=working_directory,
    )

    run_kubeconfig(
        vault_password=resolved_vault_password,
        environment=environment,
        working_directory=working_directory,
        inventory=inventory,
        out=out,
        ssh_user=ssh_user,
        master_host=master_host,
        context_name=context_name,
        env_suffix=env_suffix,
        make_current=make_current,
        shared_dir=shared_dir,
        version=version,
        refresh=refresh,
        repo_url=repo_url,
        direct=direct,
        ssh_identity=ssh_identity,
        ssh_port=ssh_port,
    )


@ansible.command("backup")
@click.option(
    "--vault-password",
    "--vault_password",
    "vault_password",
    help="Vault password",
)
@click.option("--environment", required=True, help="Target environment")
@click.option(
    "--working-directory",
    "--working_directory",
    "working_directory",
    default=".",
    show_default=True,
)
@click.option(
    "--backup-dir", "--backup_dir", default=None, help="Override backup directory"
)
@click.option("--playbook", default="backup-playbook.yml", show_default=True)
@click.option(
    "--shared-dir", "--shared_dir", default=".shared-roles", show_default=True
)
@click.option("--version", default="main", show_default=True)
@click.option(
    "--refresh/--no-refresh",
    default=True,
    show_default=True,
    help="Refresh `.shared-roles` from git instead of reusing an existing exported copy",
)
@click.option(
    "--repo-url",
    "--repo_url",
    default=None,
    help="Override shared roles repository URL",
)
def ansible_backup(
    vault_password,
    environment,
    working_directory,
    backup_dir,
    playbook,
    shared_dir,
    version,
    refresh,
    repo_url,
):
    """Run a backup playbook."""
    from cli.ansible_commands import resolve_vault_password, run_backup

    resolved_vault_password = resolve_vault_password(
        vault_password=vault_password,
        working_directory=working_directory,
    )

    run_backup(
        vault_password=resolved_vault_password,
        environment=environment,
        working_directory=working_directory,
        backup_dir=backup_dir,
        playbook=playbook,
        shared_dir=shared_dir,
        version=version,
        refresh=refresh,
        repo_url=repo_url,
    )


@ansible.command("dns")
@click.option(
    "--vault-password",
    "--vault_password",
    "vault_password",
    help="Vault password",
)
@click.option("--environment", required=True, help="Target environment")
@click.option(
    "--working-directory",
    "--working_directory",
    "working_directory",
    default=".",
    show_default=True,
)
@click.option(
    "--shared-dir", "--shared_dir", default=".shared-roles", show_default=True
)
@click.option("--version", default="main", show_default=True)
@click.option(
    "--refresh/--no-refresh",
    default=True,
    show_default=True,
    help="Refresh `.shared-roles` from git instead of reusing an existing exported copy",
)
@click.option(
    "--repo-url",
    "--repo_url",
    default=None,
    help="Override shared roles repository URL",
)
def ansible_dns(
    vault_password,
    environment,
    working_directory,
    shared_dir,
    version,
    refresh,
    repo_url,
):
    """Point the domains at the cluster through Hetzner DNS, without provisioning.

    For startups attached to a shared cluster (or on their own server) whose
    Vault holds a Hetzner token for the project that owns the DNS zone.
    """
    from cli.ansible_commands import resolve_vault_password, run_dns

    run_dns(
        vault_password=resolve_vault_password(
            vault_password=vault_password, working_directory=working_directory
        ),
        environment=environment,
        working_directory=working_directory,
        shared_dir=shared_dir,
        version=version,
        refresh=refresh,
        repo_url=repo_url,
    )


@ansible.command("update-vms")
@click.option(
    "--vault-password",
    "--vault_password",
    "vault_password",
    help="Vault password",
)
@click.option("--environment", required=True, help="Target environment")
@click.option(
    "--working-directory",
    "--working_directory",
    "working_directory",
    default=".",
    show_default=True,
)
@click.option(
    "--playbook",
    default="update-vms-playbook.yml",
    show_default=True,
    help="Playbook that updates VM packages",
)
@click.option(
    "--limit",
    default=None,
    help="Optional extra Ansible limit expression within the environment",
)
@click.option(
    "--reboot/--no-reboot",
    default=False,
    show_default=True,
    help="Reboot hosts after package upgrades when required",
)
@click.option(
    "--shared-dir", "--shared_dir", default=".shared-roles", show_default=True
)
@click.option("--version", default="main", show_default=True)
@click.option(
    "--refresh/--no-refresh",
    default=True,
    show_default=True,
    help="Refresh `.shared-roles` from git instead of reusing an existing exported copy",
)
@click.option(
    "--repo-url",
    "--repo_url",
    default=None,
    help="Override shared roles repository URL",
)
def ansible_update_vms(
    vault_password,
    environment,
    working_directory,
    playbook,
    limit,
    reboot,
    shared_dir,
    version,
    refresh,
    repo_url,
):
    """Update Hetzner VM packages via Ansible playbook."""
    from cli.ansible_commands import resolve_vault_password, run_update_vms
    from cli.cluster_commands import require_cluster_owner

    require_cluster_owner(Path(working_directory).expanduser().resolve(), "update-vms")

    resolved_vault_password = resolve_vault_password(
        vault_password=vault_password,
        working_directory=working_directory,
    )

    run_update_vms(
        vault_password=resolved_vault_password,
        environment=environment,
        working_directory=working_directory,
        playbook=playbook,
        limit=limit,
        reboot=reboot,
        shared_dir=shared_dir,
        version=version,
        refresh=refresh,
        repo_url=repo_url,
    )


@ansible.command("os-upgrade")
@click.option("--environment", required=True, help="Target environment")
@click.option("--working-directory", default=".", show_default=True)
@click.option(
    "--target-version", required=True, help="Exact Ubuntu LTS target, for example 26.04"
)
@click.option(
    "--execute", is_flag=True, help="Perform the previewed upgrade, including reboots"
)
@click.option("--dry-run", is_flag=True, help="Explicit preview (also the default)")
@click.option(
    "--backup-confirmed",
    is_flag=True,
    help="Confirm verified backups and a recovery plan",
)
@click.option(
    "--health-url",
    "health_urls",
    multiple=True,
    help="HTTPS application check before and after each node; repeat for multiple apps",
)
@click.option("--limit", default=None, help="Restrict hosts within the environment")
@click.option("--shared-dir", default=".shared-roles", show_default=True)
@click.option("--version", default="main", show_default=True)
@click.option("--refresh/--no-refresh", default=True, show_default=True)
@click.option("--repo-url", default=None, help="Override shared roles repository URL")
def ansible_os_upgrade(
    environment,
    working_directory,
    target_version,
    execute,
    dry_run,
    backup_confirmed,
    health_urls,
    limit,
    shared_dir,
    version,
    refresh,
    repo_url,
):
    """Preview or perform an Ubuntu LTS release upgrade, one node at a time.

    Package updates remain a separate operation: use update-vms for those.
    Execution requires verified backups and at least one HTTPS health URL.
    Release upgrades can interrupt applications, especially on single-node clusters.
    """
    from cli.ansible_commands import run_os_upgrade

    if execute and dry_run:
        raise click.UsageError("--execute and --dry-run cannot be combined")
    if execute and not backup_confirmed:
        raise click.UsageError("--execute requires --backup-confirmed")
    if execute and not health_urls:
        raise click.UsageError("--execute requires at least one --health-url")
    run_os_upgrade(
        vault_password=None,
        environment=environment,
        working_directory=working_directory,
        target_version=target_version,
        execute=execute,
        backup_confirmed=backup_confirmed,
        health_urls=health_urls,
        limit=limit,
        shared_dir=shared_dir,
        version=version,
        refresh=refresh,
        repo_url=repo_url,
    )


@ansible.command("k3s-upgrade")
@click.option(
    "--vault-password",
    "--vault_password",
    "vault_password",
    help="Vault password",
)
@click.option("--environment", required=True, help="Target environment")
@click.option(
    "--working-directory",
    "--working_directory",
    "working_directory",
    default=".",
    show_default=True,
)
@click.option(
    "--playbook",
    default="k3s-upgrade-playbook.yml",
    show_default=True,
    help="Playbook that upgrades k3s",
)
@click.option(
    "--k3s-version",
    "--k3s_version",
    "k3s_version",
    default=None,
    help=(
        "Target k3s version, e.g. v1.36.3+k3s1. Defaults to k3s_version from "
        "the shared k3s role. Only one minor step at a time is supported."
    ),
)
@click.option(
    "--limit",
    default=None,
    help="Optional extra Ansible limit expression within the environment",
)
@click.option(
    "--shared-dir", "--shared_dir", default=".shared-roles", show_default=True
)
@click.option("--version", default="main", show_default=True)
@click.option(
    "--refresh/--no-refresh",
    default=True,
    show_default=True,
    help="Refresh `.shared-roles` from git instead of reusing an existing exported copy",
)
@click.option(
    "--repo-url",
    "--repo_url",
    default=None,
    help="Override shared roles repository URL",
)
def ansible_k3s_upgrade(
    vault_password,
    environment,
    working_directory,
    playbook,
    k3s_version,
    limit,
    shared_dir,
    version,
    refresh,
    repo_url,
):
    """Upgrade k3s cluster-wide: control plane first, then workers."""
    from cli.ansible_commands import resolve_vault_password, run_k3s_upgrade
    from cli.cluster_commands import require_cluster_owner

    require_cluster_owner(Path(working_directory).expanduser().resolve(), "k3s-upgrade")

    resolved_vault_password = resolve_vault_password(
        vault_password=vault_password,
        working_directory=working_directory,
    )

    run_k3s_upgrade(
        vault_password=resolved_vault_password,
        environment=environment,
        working_directory=working_directory,
        playbook=playbook,
        k3s_version=k3s_version,
        limit=limit,
        shared_dir=shared_dir,
        version=version,
        refresh=refresh,
        repo_url=repo_url,
    )


@ansible.command("cert-manager-upgrade")
@click.option(
    "--vault-password",
    "--vault_password",
    "vault_password",
    help="Vault password",
)
@click.option("--environment", required=True, help="Target environment")
@click.option(
    "--working-directory",
    "--working_directory",
    "working_directory",
    default=".",
    show_default=True,
)
@click.option(
    "--cert-manager-version",
    "--cert_manager_version",
    "cert_manager_version",
    default=None,
    help=(
        "Target chart version, e.g. v1.21.2. Defaults to cert_manager_chart_version "
        "from the shared cert-manager role."
    ),
)
@click.option(
    "--shared-dir", "--shared_dir", default=".shared-roles", show_default=True
)
@click.option("--version", default="main", show_default=True)
@click.option(
    "--refresh/--no-refresh",
    default=True,
    show_default=True,
    help="Refresh `.shared-roles` from git instead of reusing an existing exported copy",
)
@click.option(
    "--repo-url",
    "--repo_url",
    default=None,
    help="Override shared roles repository URL",
)
def ansible_cert_manager_upgrade(
    vault_password,
    environment,
    working_directory,
    cert_manager_version,
    shared_dir,
    version,
    refresh,
    repo_url,
):
    """Upgrade cert-manager cluster-wide to the pinned chart version."""
    from cli.ansible_commands import resolve_vault_password, run_cert_manager_upgrade
    from cli.cluster_commands import require_cluster_owner

    require_cluster_owner(
        Path(working_directory).expanduser().resolve(), "cert-manager-upgrade"
    )

    resolved_vault_password = resolve_vault_password(
        vault_password=vault_password,
        working_directory=working_directory,
    )

    run_cert_manager_upgrade(
        vault_password=resolved_vault_password,
        environment=environment,
        working_directory=working_directory,
        cert_manager_version=cert_manager_version,
        shared_dir=shared_dir,
        version=version,
        refresh=refresh,
        repo_url=repo_url,
    )


@ansible.command("restore")
@click.option(
    "--vault-password",
    "--vault_password",
    "vault_password",
    help="Vault password",
)
@click.option("--environment", required=True, help="Target environment")
@click.option(
    "--working-directory",
    "--working_directory",
    "working_directory",
    default=".",
    show_default=True,
)
@click.option(
    "--backup-dir",
    "--backup_dir",
    default=None,
    help="Directory containing backup artifacts (defaults to ~/Backups/<project>)",
)
@click.option(
    "--db-file", "--db_file", default=None, help="Specific database dump to restore"
)
@click.option(
    "--media-file",
    "--media_file",
    default=None,
    help="Specific media archive to restore",
)
@click.option("--playbook", default="restore-playbook.yml", show_default=True)
@click.option(
    "--restore-db/--no-restore-db",
    default=True,
    show_default=True,
    help="Restore the PostgreSQL database",
)
@click.option(
    "--restore-media/--no-restore-media",
    default=True,
    show_default=True,
    help="Restore the media volume",
)
@click.option(
    "--yes",
    is_flag=True,
    help="Confirm destructive restore into the selected environment",
)
@click.option(
    "--shared-dir", "--shared_dir", default=".shared-roles", show_default=True
)
@click.option("--version", default="main", show_default=True)
@click.option(
    "--refresh/--no-refresh",
    default=True,
    show_default=True,
    help="Refresh `.shared-roles` from git instead of reusing an existing exported copy",
)
@click.option(
    "--repo-url",
    "--repo_url",
    default=None,
    help="Override shared roles repository URL",
)
def ansible_restore(
    vault_password,
    environment,
    working_directory,
    backup_dir,
    db_file,
    media_file,
    playbook,
    restore_db,
    restore_media,
    yes,
    shared_dir,
    version,
    refresh,
    repo_url,
):
    """Run a restore playbook."""
    from cli.ansible_commands import resolve_vault_password, run_restore

    resolved_vault_password = resolve_vault_password(
        vault_password=vault_password,
        working_directory=working_directory,
    )

    run_restore(
        vault_password=resolved_vault_password,
        environment=environment,
        working_directory=working_directory,
        backup_dir=backup_dir,
        db_file=db_file,
        media_file=media_file,
        playbook=playbook,
        restore_db=restore_db,
        restore_media=restore_media,
        confirm=yes,
        shared_dir=shared_dir,
        version=version,
        refresh=refresh,
        repo_url=repo_url,
    )
