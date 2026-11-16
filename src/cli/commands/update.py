"""``startup update``: adopt a release of the shared deployment in one project."""

import click


@click.command("update")
@click.option(
    "--working-directory",
    default="deployment",
    show_default=True,
    help="The project's deployment directory",
)
@click.option("--to", default=None, help="Release tag or commit to move to")
@click.option(
    "--check", is_flag=True, help="Show the available release without changing files"
)
@click.option(
    "--source-version",
    default="main",
    show_default=True,
    help="Shared deploy template branch, tag or commit to publish as the release",
)
def update(working_directory, to, check, source_version):
    """Move this project to a release of the shared roles and workflows.

    Publishes the current shared deploy template as a release in your GitHub
    account, without moving its main, and points this project's role pin and
    workflow references at it. Other projects are not affected. Application
    template files are updated separately with `startup template update`.
    """
    from cli.project_update import run_update

    run_update(working_directory, to=to, check=check, source_version=source_version)
