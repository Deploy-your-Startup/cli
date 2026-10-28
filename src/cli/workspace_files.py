"""Snapshot and publish generated workspace files without overwriting owner edits."""

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

import click


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def linked(path: Path, root: Path) -> bool:
    return any(
        parent.is_symlink()
        for parent in [path, *path.parents]
        if parent == root or root in parent.parents
    )


def inspect_files(destination: Path, files: dict) -> str:
    for name, checksum in files.items():
        path = destination / name
        if linked(path, destination):
            return f"Symlink conflicts with generated file: {name}"
        if not path.is_file():
            return f"Generated file missing: {name}"
        if digest(path) != checksum:
            return f"Generated file changed: {name}"
    return (
        "Recorded files match."
        if files
        else "Folder exists; rendering not yet completed."
    )


def snapshot_files(stage: Path) -> tuple[dict, list]:
    files, directories = {}, []
    for path in stage.rglob("*"):
        if path.is_symlink():
            raise click.ClickException(
                "Workspace templates must not generate symlinks."
            )
        if path.is_dir():
            directories.append(path.relative_to(stage).as_posix())
        if path.is_file():
            files[path.relative_to(stage).as_posix()] = digest(path)
    if not files:
        raise click.ClickException("Template generated no files.")
    return files, directories


def relative_path(name: str) -> Path:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        raise click.ClickException("Invalid generated file path in workspace state.")
    return path


def validate_files(destination: Path, stage: Path, files: dict) -> None:
    for name, checksum in files.items():
        path = destination / relative_path(name)
        original = stage / name
        if (
            linked(original, stage)
            or not original.is_file()
            or digest(original) != checksum
        ):
            raise click.ClickException(
                f"Saved template file missing or changed: {name}"
            )
        if linked(path, destination):
            raise click.ClickException(f"Symlink conflicts with generated file: {name}")
        if path.exists() and (not path.is_file() or digest(path) != checksum):
            raise click.ClickException(f"Existing file differs; preserved: {path}")


def copy_file(original: Path, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".startup-copy-")
    try:
        with os.fdopen(fd, "wb") as stream, original.open("rb") as source:
            shutil.copyfileobj(source, stream)
            stream.flush()
            os.fsync(stream.fileno())
        shutil.copymode(original, temporary)
        os.link(temporary, path)
    finally:
        os.unlink(temporary)


def publish_files(destination: Path, stage: Path, data: dict) -> None:
    validate_files(destination, stage, data["files"])
    for name in data.get("directories", []):
        path = destination / relative_path(name)
        if linked(path, destination) or (path.exists() and not path.is_dir()):
            raise click.ClickException(
                f"Existing path conflicts with generated directory: {name}"
            )
    destination.mkdir(parents=True, exist_ok=True)
    for name in data.get("directories", []):
        (destination / name).mkdir(parents=True, exist_ok=True)
    for name in data["files"]:
        path = destination / name
        if not path.exists():
            copy_file(stage / name, path)
