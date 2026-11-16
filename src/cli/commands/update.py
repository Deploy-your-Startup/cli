"""``startup update``: adopt a release of the shared deployment in one project."""

import click


@click.command("update")
@click.option(
    "--working-directory",
    default="deployment",
    show_default=True,
    help="The project's deployment directory",
)
@click.option(
    "--version",
    "--to",
    "version",
    default=None,
    help="Release, template version or commit to move to [default: latest template]",
)
@click.option(
    "--check", is_flag=True, help="Show the available release without changing files"
)
def update(working_directory, version, check):
    """Move this project to a release of the shared roles and workflows.

    Without --version, publishes the current shared deploy template as a
    release in your GitHub account, without moving its main, and points this
    project's role pin and workflow references at it. --version selects a
    release that exists there (also an earlier one, to roll back) or a template
    version such as v1.2.0 to publish and adopt. Other projects are not
    affected. Application template files are updated separately with
    `startup template update`.
    """
    from cli.project_update import run_update

    run_update(working_directory, version=version, check=check)
