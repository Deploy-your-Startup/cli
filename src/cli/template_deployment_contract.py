"""Refuse an application template that needs a newer deployment contract first."""

from pathlib import Path

import click
import yaml
from copier import Worker

from cli.project_runtime import require_project_runtime
from cli.project_version import project_version


def require_template_deployment(project: Path, source: str, version: str) -> None:
    with Worker(
        src_path=source, vcs_ref=version, skip_tasks=True, quiet=True
    ) as worker:
        manifest = worker.template.local_abspath / "startup-template.yml"
        data = yaml.safe_load(manifest.read_text()) if manifest.exists() else {}
    if not isinstance(data, dict) or data.get("deployment_version") != "single":
        return
    if project_version(project) is None:
        raise click.ClickException(
            "This template requires the single deployment version. "
            "Run startup update to migrate the shared deployment first, "
            "then retry the template update. No application files were changed."
        )
    require_project_runtime(project / "deployment")
