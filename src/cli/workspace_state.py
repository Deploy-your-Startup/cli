"""Durable local workspace creation, separate from owner files and credentials."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import click

from cli.workspace_files import inspect_files, publish_files, snapshot_files


class WorkspaceState:
    def __init__(self, directory: Path, data: dict):
        self.directory = directory
        self.data = data

    @classmethod
    def find(cls, destination: Path | None = None) -> WorkspaceState | None:
        root = Path(click.get_app_dir("startup")) / "workspaces"
        if destination is not None:
            key = hashlib.sha256(os.fsencode(destination)).hexdigest()
            paths = [root / key / "state.json"]
        else:
            paths = sorted(
                root.glob("*/state.json"),
                key=lambda p: p.stat().st_mtime_ns,
                reverse=True,
            )
        for path in paths:
            if path.is_file():
                try:
                    data = json.loads(path.read_text())
                    if data["schema"] != 1 or not isinstance(data["parameters"], dict):
                        raise ValueError("Unsupported workspace state")
                    return cls(path.parent, data)
                except (ValueError, KeyError, TypeError) as exc:
                    raise click.ClickException(
                        f"Cannot read workspace state: {path}"
                    ) from exc
        return None

    @staticmethod
    def validate_source(source: str) -> None:
        url = urlsplit(source)
        if (
            url.password
            or (url.username and url.scheme != "ssh")
            or url.query
            or url.fragment
        ):
            raise click.ClickException(
                "Use a credential-free template URL; authenticate through Git."
            )

    @classmethod
    def start(
        cls, destination: Path, source: str, version: str, parameters: dict[str, str]
    ) -> WorkspaceState:
        # URL credentials must never be persisted. SSH's conventional git user is public.
        cls.validate_source(source)
        key = hashlib.sha256(os.fsencode(destination)).hexdigest()
        directory = Path(click.get_app_dir("startup")) / "workspaces" / key
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        state = cls(
            directory,
            {
                "schema": 1,
                "destination": str(destination),
                "source": source,
                "version": version,
                "parameters": parameters,
                "step": "render",
                "last_completed": "plan",
                "failure": None,
                "files": {},
            },
        )
        state.save()
        return state

    def save(self) -> None:
        self.data["updated_at"] = datetime.now(UTC).isoformat()
        fd, temporary = tempfile.mkstemp(dir=self.directory, prefix="state-")
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(self.data, stream, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory / "state.json")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @property
    def destination(self) -> Path:
        return Path(self.data["destination"])

    def inspect(self) -> str:
        destination = self.destination
        if destination.is_symlink():
            return "Destination is a symlink; cannot resume."
        if not destination.exists():
            return "Destination folder does not exist."
        if not destination.is_dir():
            return "Destination is not a directory."
        return inspect_files(destination, self.data["files"])

    def publish(self) -> None:
        publish_files(self.destination, self.directory / "rendered", self.data)
        self.data.update(step="complete", last_completed="files", failure=None)
        self.save()

    def render(self, render) -> None:
        with tempfile.TemporaryDirectory(
            dir=self.directory, prefix="render-"
        ) as temporary:
            stage = Path(temporary) / "files"
            render(
                stage,
                self.data["parameters"],
                source=self.data["source"],
                version=self.data["version"],
            )
            files, directories = snapshot_files(stage)
            stored = self.directory / "rendered"
            if stored.exists():
                shutil.rmtree(stored)
            stage.rename(stored)
            self.data.update(
                files=files,
                directories=directories,
                step="publish",
                last_completed="render",
                failure=None,
            )
            self.save()

    def run(self, render) -> None:
        try:
            if self.data["step"] == "render":
                self.render(render)
            if self.data["step"] == "publish":
                self.publish()
            elif self.inspect() != "Recorded files match.":
                raise click.ClickException(self.inspect())
        except (Exception, KeyboardInterrupt) as exc:
            # Store only the exception type: arbitrary tool errors may contain credentials.
            self.data["failure"] = type(exc).__name__
            self.save()
            if isinstance(exc, (KeyboardInterrupt, click.ClickException)):
                raise
            raise click.ClickException(str(exc)) from exc
