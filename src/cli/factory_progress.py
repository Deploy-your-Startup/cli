"""Persist and inspect Factory creation and local setup across CLI processes."""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import click

from cli.workspace_state import WorkspaceState


def saved_factory(destination: Path | None = None) -> WorkspaceState:
    state = WorkspaceState.find(destination)
    if state is None or not state.data.get("factory"):
        raise click.ClickException("No saved Factory setup found.")
    return state


def resume_plan(destination, source, version, resume):
    if not resume:
        return (
            destination or Path.home() / "Projects/startup-factory",
            source,
            version,
            None,
        )
    state = saved_factory(destination.expanduser().absolute() if destination else None)
    if (source and source != state.data["source"]) or (
        version and version != state.data["version"]
    ):
        raise click.ClickException("Resume options differ from the saved Factory plan.")
    return state.destination, state.data["source"], state.data["version"], state


def checkpoint(state: WorkspaceState, name: str, operation):
    state.data.update(workflow_step=name, workflow_failure=None)
    state.save()
    try:
        result = operation()
    except (Exception, KeyboardInterrupt) as exc:
        state.data["workflow_failure"] = type(exc).__name__
        state.save()
        click.echo(
            f"Factory progress saved: {state.directory / 'state.json'}", err=True
        )
        click.echo(
            f"Inspect: startup factory status --project-dir {shlex.quote(str(state.destination))}",
            err=True,
        )
        click.echo(
            f"Resume: startup factory init --resume --project-dir {shlex.quote(str(state.destination))}",
            err=True,
        )
        raise
    state.data["workflow_last_completed"] = name
    state.save()
    return result


def setup_workspace(state: WorkspaceState) -> None:
    destination = state.destination
    commands = (
        ("trust", ["mise", "trust", str(destination / "mise.toml")]),
        ("runtimes", ["mise", "install"]),
        ("tooling", ["mise", "exec", "--", "./scripts/init.sh"]),
    )
    # Re-run idempotent setup to check actual tools and repositories, even when
    # an earlier process recorded a successful step. A flag is not proof of disk state.
    for name, command in commands:
        checkpoint(
            state,
            name,
            lambda command=command: subprocess.run(
                command, check=True, cwd=destination
            ),
        )


def show_status(destination: Path | None) -> None:
    state = saved_factory(destination.expanduser().absolute() if destination else None)
    fields = {
        "Folder": str(state.destination),
        "Template": state.data["source"],
        "Version": state.data["version"],
        "Last completed": state.data.get("workflow_last_completed", "plan"),
        "Current step": state.data.get("workflow_step", "files"),
        "Last failure": state.data.get("workflow_failure")
        or state.data["failure"]
        or "None",
        "Files": state.inspect(),
        "State file": str(state.directory / "state.json"),
    }
    for label, value in fields.items():
        click.echo(f"{label}: {value}")
    click.echo(
        "Setup progress records previous command results; resume rechecks local setup. Cloud deployment is separate."
    )
