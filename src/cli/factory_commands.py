"""Local Factory onboarding and owner-preserving startup registration."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import tempfile
from pathlib import Path

import click
import yaml

from cli import wizard_output as ui
from cli.factory_prerequisites import ensure_factory_tools
from cli.factory_progress import checkpoint, resume_plan, setup_workspace, show_status
from cli.template_commands import ANSWERS_FILE, render_project
from cli.workspace_state import WorkspaceState

FACTORY_TEMPLATE = "https://github.com/Deploy-your-Startup/startup-factory-template.git"
FACTORY_VERSION = "v0.1.22"
MARKER = ".startup-factory.yml"


def is_factory(root: Path) -> bool:
    marker = root / MARKER
    if marker.is_symlink():
        raise click.ClickException("Factory marker must not be a symlink.")
    try:
        return marker.is_file() and yaml.safe_load(marker.read_text()) == {"schema": 1}
    except yaml.YAMLError as exc:
        raise click.ClickException(
            "Invalid Factory marker; review .startup-factory.yml."
        ) from exc


def factory_for_output(output: Path) -> Path | None:
    root = output.resolve().parent.parent
    return (
        root
        if output.resolve() == root / "projects/startups" and is_factory(root)
        else None
    )


def factory_in_directory(directory: Path) -> Path | None:
    for root in (directory.resolve(), *directory.resolve().parents):
        if is_factory(root):
            return root
    return None


def registry_document(root: Path) -> tuple[str, dict]:
    path = root / "projects.yaml"
    if path.is_symlink():
        raise click.ClickException("Factory registry must not be a symlink.")
    text = path.read_text()
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise click.ClickException(
            "Invalid Factory projects.yaml; fix it before bootstrap."
        ) from exc
    if not isinstance(data, dict) or not isinstance(data.get("projects"), list):
        raise click.ClickException(
            "Factory projects.yaml must contain a projects list."
        )
    entries = data["projects"]
    if any(
        not isinstance(entry, dict)
        or not isinstance(entry.get("name"), str)
        or not isinstance(entry.get("repo"), str)
        for entry in entries
    ):
        raise click.ClickException(
            "Each Factory project needs a name and repository URL."
        )
    if len({entry["name"] for entry in entries}) != len(entries):
        raise click.ClickException("Factory registry contains duplicate project names.")
    return text, data


def check_registration(output: Path, name: str, owner: str) -> None:
    root = factory_for_output(output)
    if root:
        _, data = registry_document(root)
        existing = next(
            (entry for entry in data["projects"] if entry["name"] == name), None
        )
        if (
            existing
            and canonical_remote(existing["repo"])
            != f"https://github.com/{owner}/{name}"
        ):
            raise click.ClickException(
                "Factory project already points at another repository; review projects.yaml before bootstrap."
            )
        tooling = data.get("tooling", [])
        if not isinstance(tooling, list) or any(
            not isinstance(entry, dict) for entry in tooling
        ):
            raise click.ClickException(
                "Factory tooling must be a list of repository entries."
            )
        if any(entry.get("name") == name for entry in tooling):
            raise click.ClickException(
                "Project name is already used by Factory tooling."
            )


def canonical_remote(repo: str) -> str:
    return repo.removesuffix(".git").replace("git@github.com:", "https://github.com/")


def register_project(project: Path) -> None:
    root = factory_for_output(project.parent)
    if root is None or not (project / ".git").exists():
        return
    result = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        cwd=project,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        return  # No remote yet: the resumable bootstrap has not created its repository.
    repo = result.stdout.strip()
    if not re.fullmatch(
        r"(?:https://github\.com/|git@github\.com:)[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?",
        repo,
    ):
        raise click.ClickException(
            "Factory registration requires a GitHub remote without credentials."
        )
    text, data = registry_document(root)
    existing = next(
        (entry for entry in data["projects"] if entry["name"] == project.name), None
    )
    if existing is not None:
        if canonical_remote(existing["repo"]) != canonical_remote(repo):
            raise click.ClickException(
                f"Factory project {project.name} already points at another repository; review projects.yaml."
            )
        return
    # Replace only the YAML list span, keeping surrounding owner comments and tooling intact.
    document = yaml.compose(text)
    assert isinstance(document, yaml.MappingNode)
    node = next(value for key, value in document.value if key.value == "projects")
    assert isinstance(node, yaml.SequenceNode)
    entry = {"name": project.name, "repo": repo}
    if node.flow_style:
        replacement = yaml.safe_dump(
            [*data["projects"], entry], sort_keys=False
        ).rstrip()
        replacement = "\n" + "\n".join("  " + line for line in replacement.splitlines())
        updated = (
            text[: node.start_mark.index] + replacement + text[node.end_mark.index :]
        )
    else:
        indent = node.start_mark.column
        addition = yaml.safe_dump([entry], sort_keys=False)
        addition = "".join(" " * indent + line + "\n" for line in addition.splitlines())
        last = node.value[-1]
        # Locate the last entry's final content line, excluding trailing comments.
        end = max(value.end_mark.index for _, value in last.value)
        end = text.find("\n", end)
        end = len(text) if end == -1 else end + 1
        updated = text[:end].rstrip("\n") + "\n" + addition + text[end:]
    path = root / "projects.yaml"
    with tempfile.NamedTemporaryFile(
        mode="w", dir=root, prefix=".projects-", delete=False
    ) as temporary:
        temporary.write(updated)
        pending = Path(temporary.name)
    try:
        pending.chmod(path.stat().st_mode & 0o777)
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)
    ui.success(f"Registered {project.name} in {path}.")


def resolve_source(source: str) -> str:
    if Path(source).expanduser().exists():
        return str(Path(source).expanduser().resolve())
    return source


def destination_problem(destination: Path, source: str) -> str | None:
    """Explain why a folder cannot hold this Factory, or None when it can."""
    if destination.is_symlink():
        return "Factory destination must not be a symlink."
    if destination.exists() and not destination.is_dir():
        return "Destination must be empty or an existing Factory; no files changed."
    if not destination.exists() or not any(destination.iterdir()):
        return None
    if not is_factory(destination) or (
        not (destination / ANSWERS_FILE).is_file()
        or (destination / ANSWERS_FILE).is_symlink()
    ):
        return "Destination must be empty or an existing Factory; no files changed."
    answers = yaml.safe_load((destination / ANSWERS_FILE).read_text())
    if not isinstance(answers, dict) or answers.get("_src_path") != source:
        return "Existing Factory uses another template; review its Copier answers."
    return None


def suggested_destination(source: str) -> Path:
    """The default folder, or the next free variant when another folder owns it."""
    base = Path.home() / "Projects/startup-factory"
    candidate, number = base, 2
    while destination_problem(candidate, source):
        candidate, number = base.with_name(f"{base.name}-{number}"), number + 1
    return candidate


def creation_state(
    destination: Path, source: str, version: str, setup: bool
) -> WorkspaceState:
    state = WorkspaceState.find(destination)
    if state and (
        state.data["source"] != source
        or (state.data["step"] != "complete" and state.data["version"] != version)
    ):
        raise click.ClickException(
            "Factory plan differs from saved creation; use --resume."
        )
    if state is None or state.data["step"] == "complete":
        problem = destination_problem(destination, source)
        if problem:
            raise click.ClickException(problem)
    if state is None:
        state = WorkspaceState.start(
            destination, source, version, {"project_name": destination.name}
        )
        if destination.exists() and any(destination.iterdir()):
            state.data.update(step="complete", last_completed="existing Factory")
    state.data.update(
        factory=True, setup_requested=setup or state.data.get("setup_requested", False)
    )
    state.save()
    return state


def prepare_factory_files(state: WorkspaceState) -> None:
    destination = state.destination
    if state.data["step"] == "complete":
        ui.info(f"Using existing Factory at {destination}; owner files are preserved.")
    else:
        state.run(render_project)
        ui.success(f"Factory created at {destination}.")
    if not is_factory(destination):
        raise click.ClickException(
            "Selected template does not declare a supported Factory schema."
        )
    registry_document(destination)


def initialize_factory(
    destination: Path,
    source: str,
    version: str,
    setup: bool,
    show_next_steps: bool = True,
) -> Path:
    source = resolve_source(source)
    WorkspaceState.validate_source(source)
    destination = destination.expanduser().absolute()
    state = creation_state(destination, source, version, setup)
    if setup:
        checkpoint(state, "prerequisites", ensure_factory_tools)
    checkpoint(state, "files", lambda: prepare_factory_files(state))
    if setup:
        setup_workspace(state)
    state.data.update(
        workflow_step="complete" if setup else "setup pending", workflow_failure=None
    )
    state.save()
    if show_next_steps:
        ui.info(f"Next: cd {shlex.quote(str(destination))} && startup bootstrap")
        ui.info(
            "Review the files, initialize Git and commit .copier-answers.yml before template updates."
        )
        ui.info("Sprowi is optional: mise exec -- ./scripts/init.sh --with-sprowi")
    return destination


@click.group()
def factory():
    """Create your local startup portfolio and reusable agent workspace."""


@factory.command("init")
@click.option(
    "--project-dir",
    type=click.Path(file_okay=False, path_type=Path),
    help="Factory folder (default: ~/Projects/startup-factory; latest saved folder with --resume).",
)
@click.option("--template", "source", help="Factory template URL or path.")
@click.option("--version", help="Factory template release; defaults to the CLI pin.")
@click.option(
    "--setup",
    is_flag=True,
    help="Check tools, install workspace runtimes and clone registered tooling.",
)
@click.option(
    "--resume",
    is_flag=True,
    help="Continue the saved Factory plan (latest by default).",
)
def init(
    project_dir: Path | None,
    source: str | None,
    version: str | None,
    setup: bool,
    resume: bool,
):
    """Create or resume a Factory without provisioning cloud resources."""
    try:
        project_dir, source, version, state = resume_plan(
            project_dir, source, version, resume
        )
        initialize_factory(
            project_dir,
            source or FACTORY_TEMPLATE,
            version or FACTORY_VERSION,
            setup or bool(state and state.data.get("setup_requested")),
        )
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise click.ClickException(str(exc)) from exc


@factory.command("status")
@click.option("--project-dir", type=click.Path(file_okay=False, path_type=Path))
def status(project_dir: Path | None):
    """Show saved Factory progress and inspect its folder (latest by default)."""
    show_status(project_dir)


@factory.command("onboard")
@click.option("--project-dir", type=click.Path(file_okay=False, path_type=Path))
@click.option("--template", "source", hidden=True)
@click.option("--version", hidden=True)
@click.option(
    "--resume", is_flag=True, help="Resume the saved Factory and continue onboarding."
)
@click.pass_context
def onboard(
    ctx: click.Context,
    project_dir: Path | None,
    source: str | None,
    version: str | None,
    resume: bool,
):
    """Set up your Factory, then guide you through your first startup."""
    if resume:
        project_dir, source, version, _state = resume_plan(
            project_dir, source, version, True
        )
    source = resolve_source(source or FACTORY_TEMPLATE)
    version = version or FACTORY_VERSION
    if project_dir is None:
        # Suggest a free folder and ask again instead of ending onboarding when
        # the chosen folder already holds something else.
        default = suggested_destination(source)
        while True:
            project_dir = Path(
                ui.text_input(
                    "Factory folder",
                    default=str(default),
                    type=click.Path(file_okay=False),
                )
            )
            problem = destination_problem(project_dir.expanduser().absolute(), source)
            if not problem:
                break
            ui.error(f"{problem} Choose another folder.")
    try:
        destination = initialize_factory(
            project_dir, source, version, True, show_next_steps=False
        )
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        raise click.ClickException(str(exc)) from exc
    ui.info("Factory ready. Now your first startup:")
    from cli.startup import bootstrap

    ctx.invoke(bootstrap, output_dir=str(destination / "projects/startups"))


@factory.command("register")
@click.argument(
    "project", type=click.Path(exists=True, file_okay=False, path_type=Path)
)
def register(project: Path):
    """Register a startup already cloned under a Factory's projects/startups."""
    project = project.resolve()
    if factory_for_output(project.parent) is None:
        raise click.ClickException(
            "Project must be inside a Factory's projects/startups directory."
        )
    try:
        register_project(project)
    except (OSError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
