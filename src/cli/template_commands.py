"""Copier lifecycle for application templates; shared-role sync stays separate."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import click
import yaml
from copier import run_copy, run_update
from copier.main import Worker

DEFAULT_TEMPLATE = "https://github.com/Deploy-your-Startup/django-backend-template.git"
PITCH_TEMPLATE = "https://github.com/Deploy-your-Startup/pitch-template.git"
ANSWERS_FILE = ".copier-answers.yml"
PUBLIC_FIELDS = {
    "project_name",
    "base_domain",
    "github_username",
    "additional_domains",
    "deploy_repo_name",
    "docker_registry_host",
    "postgres_version",
    "k8s_namespace",
    "ci_key",
    "user_key",
}
PROTECTED = ("deployment/group_vars/**",)


def template_authentication(source: str, version: str) -> str | None:
    """Inspect public template capabilities without rendering or running tasks."""
    with Worker(
        src_path=source, vcs_ref=version, skip_tasks=True, quiet=True
    ) as worker:
        manifest = worker.template.local_abspath / "startup-template.yml"
        if not manifest.is_file():
            return None
        values = yaml.safe_load(manifest.read_text())
        provider = values.get("authentication") if isinstance(values, dict) else None
        if provider not in (None, "auth0"):
            raise click.ClickException("Unsupported template authentication provider.")
        return provider


def git(project: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=project, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def require_clean_project(project: Path) -> Path:
    project = project.resolve()
    if git(project, "rev-parse", "--show-toplevel") != str(project):
        raise click.ClickException("--project-dir must be the Git repository root.")
    if git(project, "status", "--porcelain"):
        raise click.ClickException(
            "Commit or isolate local changes before updating the template."
        )
    return project


def parse_data(values: tuple[str, ...]) -> dict[str, str]:
    data = {}
    for item in values:
        key, separator, value = item.partition("=")
        if not separator or key not in PUBLIC_FIELDS:
            raise click.BadParameter(
                "--data accepts only public template fields as KEY=VALUE."
            )
        data[key] = value
    return data


def render_project(
    project: Path,
    replacements: dict[str, str],
    *,
    source: str = DEFAULT_TEMPLATE,
    version: str = "HEAD",
) -> None:
    """Render only public parameters. Vault encryption remains in bootstrap."""
    data = {
        key.removeprefix("§§deploy_your_startup.").removesuffix("§§"): value
        for key, value in replacements.items()
    }
    if data.keys() - PUBLIC_FIELDS:
        raise click.ClickException("Template data contains an unsupported field.")
    run_copy(
        source,
        project.resolve(),
        data=data,
        vcs_ref=version,
        defaults=True,
        overwrite=True,
        skip_tasks=True,
        quiet=True,
    )


def adoption_data(project: Path) -> dict[str, str]:
    """Read public configuration without decrypting any Vault values."""
    data = {"project_name": project.name}
    for name in ("all.yml", "production.yml"):
        path = project / "deployment/group_vars" / name
        if not path.exists():
            continue
        values = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
        if not isinstance(values, dict):
            continue
        for key in PUBLIC_FIELDS - {"additional_domains", "ci_key", "user_key"}:
            value = values.get(key)
            if isinstance(value, str) and "$ANSIBLE_VAULT" not in value:
                data[key] = value
        domains = values.get("additional_domains")
        if isinstance(domains, list):
            data["additional_domains"] = "\n".join(
                f"  - {domain}" for domain in domains
            )
        keys = values.get("ssh_public_keys", [])
        for entry in keys if isinstance(keys, list) else []:
            if not isinstance(entry, dict):
                continue
            public_key = entry.get("key", "")
            if isinstance(public_key, str) and public_key.startswith("ssh-"):
                field = "ci_key" if "ci_key" in entry.get("name", "") else "user_key"
                data[field] = public_key
    remote = git(project, "remote", "get-url", "origin")
    match = re.search(r"github\.com[:/]([^/]+)/", remote)
    if match:
        data["github_username"] = match.group(1)
    return data


def adopt_project(
    project: Path, *, source: str, version: str, data: dict[str, str]
) -> None:
    project = require_clean_project(project)
    answers = project / ANSWERS_FILE
    if answers.exists() or answers.is_symlink():
        raise click.ClickException(
            "Project already has Copier metadata; use template update."
        )
    parameters = adoption_data(project)
    parameters.update(data)
    # Render the baseline elsewhere. Transferring only Copier-generated answers
    # keeps missing template files deleted and all project customizations intact.
    with tempfile.TemporaryDirectory(prefix="startup-template-adopt-") as temporary:
        generated = Path(temporary).resolve() / "generated"
        run_copy(
            source,
            generated,
            data=parameters,
            vcs_ref=version,
            defaults=True,
            quiet=True,
            skip_tasks=True,
        )
        shutil.copyfile(generated / ANSWERS_FILE, answers)


def update_project(
    project: Path, *, version: str, data: dict[str, str], dry_run: bool
) -> None:
    project = require_clean_project(project)
    if not (project / ANSWERS_FILE).is_file():
        raise click.ClickException(
            "Project has no Copier metadata; use template adopt first."
        )
    with tempfile.TemporaryDirectory(prefix="startup-template-update-") as temporary:
        target = project
        if dry_run:
            target = Path(temporary).resolve() / "project"
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--quiet",
                    "--no-hardlinks",
                    str(project),
                    str(target),
                ],
                check=True,
            )
            git(target, "remote", "remove", "origin")
        run_update(
            target,
            vcs_ref=version,
            data=data,
            defaults=True,
            overwrite=True,
            quiet=True,
            skip_tasks=True,
            skip_if_exists=PROTECTED,
        )
        conflicts = git(target, "diff", "--name-only", "--diff-filter=U")
        patch = git(target, "diff", "--", ".", ":(exclude)deployment/group_vars/**")
        if patch:
            click.echo(patch)
        new_files = git(target, "ls-files", "--others", "--exclude-standard")
        if new_files:
            click.echo(f"New files:\n{new_files}")
        if conflicts:
            raise click.ClickException(
                f"Template update has conflicts; resolve before committing:\n{conflicts}"
            )
        click.echo(
            "Preview complete; original project unchanged."
            if dry_run
            else "Template updated. Review the diff and run project checks before committing."
        )


@click.group("template")
def template():
    """Generate, adopt and update application templates with Copier."""


@template.command("adopt")
@click.option(
    "--project-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option("--template", "source", default=DEFAULT_TEMPLATE, show_default=True)
@click.option("--version", required=True, help="Explicit baseline Git tag or commit.")
@click.option(
    "--data", "values", multiple=True, help="Public template parameter KEY=VALUE."
)
def adopt(project_dir: Path, source: str, version: str, values: tuple[str, ...]):
    """Record a baseline without changing existing project files.

    Existing differences become customizations; past template changes are not
    retroactively applied. No vault operation, commit, push or deploy runs.
    """
    try:
        adopt_project(
            project_dir, source=source, version=version, data=parse_data(values)
        )
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("Copier baseline recorded. Review and commit .copier-answers.yml.")


@template.command("update")
@click.option(
    "--project-dir",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=".",
)
@click.option(
    "--version",
    default="HEAD",
    show_default=True,
    help="Target Git tag, branch or commit.",
)
@click.option(
    "--data", "values", multiple=True, help="Public template parameter KEY=VALUE."
)
@click.option(
    "--dry-run", is_flag=True, help="Update an isolated clone and show the diff."
)
def update(project_dir: Path, version: str, values: tuple[str, ...], dry_run: bool):
    """Apply template changes while preserving project customizations."""
    try:
        update_project(
            project_dir, version=version, data=parse_data(values), dry_run=dry_run
        )
    except (subprocess.CalledProcessError, OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
