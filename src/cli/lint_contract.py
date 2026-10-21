"""Check that a project keeps the shared service lint contract.

`./make.sh format` writes, `./make.sh lint` only checks, and CI calls only
`lint`. These checks read committed files; they never run the service tools.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

import click
import yaml

from cli import wizard_output as ui

SKIPPED_DIRS = {"node_modules", ".venv", "venv", "dist", "build", "template"}
HOOK_PATHS = (
    ".pre-commit-config.yaml",
    ".pre-commit-config.yml",
    ".husky",
    ".githooks",
    "lefthook.yml",
    ".lefthook.yml",
)
SERVICE_WORKFLOWS = (
    "/build-and-deploy-service.yml",
    "/build-and-deploy-service-parallel.yml",
)
ERREXIT = re.compile(r"^\s*set\s+(-[a-zA-Z]*e[a-zA-Z]*\b|-o\s+errexit)", re.MULTILINE)
REQUIREMENT_NAME = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def code_lines(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith("#")
    )


def implements(script: str, command: str) -> bool:
    """Whether make.sh dispatches `command` via an if-test or a case branch."""
    name = re.escape(command)
    test = rf"==?\s*[\"']?{name}[\"']?\s*\]"
    case = rf"^\s*(?:[\w:-]+\|)*[\"']?{name}[\"']?(?:\|[\w:-]+)*\s*\)"
    return re.search(f"{test}|{case}", script, re.MULTILINE) is not None


def service_dirs(project: Path) -> list[Path]:
    dirs = [project] if (project / "make.sh").is_file() else []
    for child in sorted(project.iterdir()):
        if (
            child.is_dir()
            and not child.name.startswith(".")
            and child.name not in SKIPPED_DIRS
            and (child / "make.sh").is_file()
        ):
            dirs.append(child)
    return dirs


def requirements(pyproject: dict) -> list[str]:
    project = pyproject.get("project", {})
    found = list(project.get("dependencies", []))
    for extra in project.get("optional-dependencies", {}).values():
        found.extend(extra)
    for group in pyproject.get("dependency-groups", {}).values():
        found.extend(item for item in group if isinstance(item, str))
    return found


def requirement_name(requirement: str) -> str:
    match = REQUIREMENT_NAME.match(requirement)
    return match.group(1).lower().replace("_", "-") if match else ""


def make_findings(service: Path, label: str) -> list[str]:
    script = code_lines((service / "make.sh").read_text())
    findings = []
    if not ERREXIT.search(script):
        findings.append(f"{label}/make.sh: add `set -e` so a failing check fails")
    has_lint, has_format = implements(script, "lint"), implements(script, "format")
    if has_lint and not has_format:
        findings.append(f"{label}/make.sh: `lint` needs a matching `format` command")
    if has_format and not has_lint:
        findings.append(f"{label}/make.sh: `format` needs a matching `lint` command")
    if re.search(r"\buvx\b", script):
        findings.append(f"{label}/make.sh: use `uv run` with pinned tools, not `uvx`")
    return findings


def python_findings(service: Path, label: str) -> list[str]:
    path = service / "pyproject.toml"
    if not path.is_file():
        return []
    try:
        pyproject = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError:
        return [f"{label}/pyproject.toml: cannot be parsed"]
    findings = []
    if not (service / ".python-version").is_file():
        findings.append(f"{label}: commit a .python-version for uv")
    entries = requirements(pyproject)
    names = {requirement_name(entry) for entry in entries}
    for entry in entries:
        if requirement_name(entry) == "ty" and not re.fullmatch(
            r"ty\s*==\s*[\w.]+", entry.strip()
        ):
            findings.append(f"{label}/pyproject.toml: pin ty exactly (ty==<version>)")
    if "django" in names and "django-stubs" not in names:
        findings.append(f"{label}/pyproject.toml: Django services need django-stubs")
    return findings


def mise_findings(directory: Path, label: str) -> list[str]:
    path = directory / "mise.toml"
    if not path.is_file():
        return []
    try:
        mise = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError:
        return [f"{label}/mise.toml: cannot be parsed"]
    findings = [
        f"{label}/mise.toml: remove `{tool}` from [tools]; services pin Python "
        "with .python-version and use the global uv"
        for tool in ("uv", "python")
        if tool in mise.get("tools", {})
    ]
    for name, task in mise.get("tasks", {}).items():
        run = task.get("run", "") if isinstance(task, dict) else task
        commands = run if isinstance(run, list) else [run]
        if any(re.search(r"\buvx\b", str(command)) for command in commands):
            findings.append(f"{label}/mise.toml: task {name} uses `uvx`; use `uv run`")
    return findings


def triggers(workflow: dict) -> set[str]:
    # PyYAML reads the bare `on` key as boolean True.
    value = workflow.get("on", workflow.get(True))
    if isinstance(value, str):
        return {value}
    return set(value or [])


def workflow_findings(project: Path) -> list[str]:
    findings = []
    for path in sorted((project / ".github" / "workflows").glob("*.y*ml")):
        label = f".github/workflows/{path.name}"
        try:
            workflow = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError:
            findings.append(f"{label}: cannot be parsed")
            continue
        jobs = workflow.get("jobs") or {}
        callers = [
            job
            for job in jobs.values()
            if isinstance(job, dict)
            and any(name in str(job.get("uses", "")) for name in SERVICE_WORKFLOWS)
        ]
        if not callers:
            continue
        if "pull_request" not in triggers(workflow):
            findings.append(
                f"{label}: add a pull_request trigger so checks run before merge"
            )
        for job in callers:
            for inputs in job_services(job.get("with") or {}):
                findings += service_gate_findings(project, label, inputs)
    return findings


def job_services(inputs: dict) -> list[dict]:
    """Expand the parallel workflow's `services` JSON like the workflow does."""
    try:
        services = json.loads(inputs.get("services") or "null")
    except (TypeError, ValueError):
        services = None
    if not isinstance(services, list):
        return [inputs]
    return [{**inputs, **item} for item in services if isinstance(item, dict)]


def service_gate_findings(project: Path, label: str, inputs: dict) -> list[str]:
    findings = []
    service = str(inputs.get("service", ""))
    make = project / service / "make.sh"
    has_lint = make.is_file() and implements(code_lines(make.read_text()), "lint")
    if inputs.get("run_lint") is True and not has_lint:
        findings.append(
            f"{label}: run_lint is true but {service}/make.sh has no `lint`"
        )
    if has_lint and inputs.get("run_lint") is not True:
        findings.append(
            f"{label}: set run_lint: true so CI gates {service} on `./make.sh lint`"
        )
    return findings


def contract_findings(project: Path) -> list[str]:
    findings = [
        f"{name}: remove Git hooks; checks run through ./make.sh lint and CI"
        for name in HOOK_PATHS
        if (project / name).exists()
    ]
    findings += mise_findings(project, ".")
    for service in service_dirs(project):
        label = "." if service == project else service.name
        findings += make_findings(service, label)
        findings += python_findings(service, label)
        if service != project:
            findings += mise_findings(service, label)
    findings += workflow_findings(project)
    return findings


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
