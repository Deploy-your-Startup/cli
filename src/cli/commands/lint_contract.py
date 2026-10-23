"""``startup lint-contract``: check the make.sh format/lint contract."""

from pathlib import Path

import click

from cli import wizard_output as ui
from cli.lint_contract import contract_findings, service_dirs


@click.command("lint-contract")
@click.option(
    "--project-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=Path("."),
    show_default=True,
    help="Project root containing the services and .github/workflows.",
)
def lint_contract(project_dir: Path):
    """Check the make.sh format/lint contract and its CI gate."""
    project = project_dir.resolve()
    services = service_dirs(project)
    if not services:
        raise click.ClickException(f"No make.sh found in {project} or its services.")
    findings = contract_findings(project)
    for finding in findings:
        ui.error(finding)
    if findings:
        raise click.ClickException(
            f"Lint contract: {len(findings)} issue(s); no files were changed."
        )
    ui.success(f"Lint contract kept by {len(services)} service(s).")
