# Shared startup clusters

The CLI supports one owner-managed Hetzner cluster serving multiple mutually
trusted startups. Create the owner with `startup bootstrap --shared-cluster`,
verify it, export public connection settings with `startup cluster export`, and
attach another startup with `startup bootstrap --cluster <descriptor>`. Each
startup needs a unique namespace, domain, Vault, database and media volumes.
The default Django release supports this contract; alternative templates must
declare it in `startup-template.yml`.

Keep cluster-wide operations in the owner project and application deployments
in their respective projects. Preserve ownership guards before credentials or
remote actions. Never silently convert an existing production project or change
its data namespace. Namespace policies separate application traffic; SSH/admin
access is shared, and local data volumes do not provide failover. Before shared
upgrades, verify backups and health for every attached application. Test owner
and attached flows together through real CLI processes and rendered templates.
