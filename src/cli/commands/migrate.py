"""``startup migrate``: one-time moves of a project's configuration."""

import click


@click.group("migrate")
def migrate():
    """One-time moves of a project's configuration."""


@migrate.command("deploy")
@click.option(
    "--working-directory",
    default="deployment",
    show_default=True,
    help="The project's deployment directory",
)
@click.option(
    "--source",
    default=None,
    help="owner/repository to use instead of Deploy-your-Startup/deploy, e.g. a fork",
)
@click.option("--dry-run", is_flag=True, help="Show the move without changing files")
def deploy(working_directory, source, dry_run):
    """Move this project from an owner copy to Deploy-your-Startup/deploy.

    Points the project's shared workflow and action references at the new
    source and pins its current release; the old pin is not reproduced.
    With --source the project uses a fork instead, recorded in
    deployment/shared-roles.source. Nothing is deployed: review the diff,
    commit and push. Archive the old owner copy once no project calls it.
    """
    from cli.project_migrate import run_migrate

    run_migrate(working_directory, source=source, dry_run=dry_run)
