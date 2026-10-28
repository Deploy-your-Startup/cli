"""Find local Factory workspaces without searching the whole home directory."""

from __future__ import annotations

import json
from pathlib import Path

import click

from cli import wizard_output as ui


def is_factory(path: Path) -> bool:
    from cli.factory_commands import MARKER
    from cli.factory_commands import is_factory as marked_factory

    if (path / MARKER).exists() or (path / MARKER).is_symlink():
        return marked_factory(path)
    # Older owner workspaces predate the explicit Factory marker.
    return (path / "projects.yaml").is_file() and (path / "scripts/init.sh").is_file()


def bootstrap_folder(*, assume_yes: bool) -> Path:
    current = Path.cwd().resolve()
    for parent in (current, *current.parents):
        if is_factory(parent):
            return parent / "projects/startups"

    projects = Path.home() / "Projects"
    factories = _known_factories(projects)
    if len(factories) == 1:
        return factories[0] / "projects/startups"
    if factories:
        if assume_yes:
            raise click.ClickException(
                "Multiple factories found. Run inside the intended factory or set --output-dir."
            )
        choice = ui.numbered_choice(
            "Where should the startup live?",
            [*(str(path) for path in factories), "Another folder"],
        )
        if choice <= len(factories):
            return factories[choice - 1] / "projects/startups"
    return projects


def _known_factories(projects: Path) -> list[Path]:
    candidates: set[Path] = set()
    # Template creation already records destinations, including custom locations.
    states = Path(click.get_app_dir("startup")) / "workspaces"
    for state in states.glob("*/state.json"):
        try:
            data = json.loads(state.read_text())
            if data.get("step") == "complete":
                destination = Path(data["destination"])
                if destination.is_absolute() and is_factory(destination):
                    candidates.add(destination.resolve())
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            continue

    # Also discover pre-existing factories in the CLI's conventional project folder.
    if projects.is_dir():
        for path in projects.iterdir():
            if is_factory(path):
                candidates.add(path.resolve())
    return sorted(candidates)
