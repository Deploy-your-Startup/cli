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
    help="Release or commit to pin, or 'latest' to follow main unpinned",
)
@click.option(
    "--check", is_flag=True, help="Show the available release without changing files"
)
def update(working_directory, version, check):
    """Move this project to a release of the shared roles and workflows.

    Without --version, adopts the latest vX.Y.Z release of the project's shared
    repository (Deploy-your-Startup/deploy, or the fork recorded in
    deployment/shared-roles.source) and points the role pin and workflow
    references at it. --version selects a release that exists there (also an
    earlier one, to roll back) or a full commit. Other projects are not
    affected. A project that still calls an owner copy is moved once with
    `startup migrate deploy`.

    `--version latest` removes the pins instead: the project then follows the
    shared repository's main and changes with every merge there, without a diff
    to review. A plain `startup update` leaves such a project alone.

    Application template files are updated separately with
    `startup template update`.
    """
    from cli.project_update import run_update

    run_update(working_directory, version=version, check=check)
