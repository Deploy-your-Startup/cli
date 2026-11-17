"""Verify materialized roles against the configured commit without another pin file."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import click


def verify_project_roles(
    working_dir: Path, root: Path, version: str, repo_url: str | None
) -> None:
    from cli.ansible_commands import (
        _git,
        _shared_roles_fingerprint,
        clone_or_update_shared_roles,
    )
    from cli.shared_source import shared_repository

    if (root / ".git").exists():
        head = _git(
            root, "rev-parse", "HEAD", cwd=working_dir, capture_output=True
        ).stdout.strip()
        dirty = _git(
            root, "status", "--porcelain", cwd=working_dir, capture_output=True
        ).stdout.strip()
        if dirty or (re.fullmatch(r"[0-9a-f]{40}", version) and head != version):
            raise click.ClickException(
                "Refusing to run modified roles or a checkout at a different deployment version."
            )
        return
    # An action export has no Git metadata. Resolve an independent expected tree
    # at the configured version and compare the actual runtime bytes, not a
    # checksum supplied by that same export.
    with tempfile.TemporaryDirectory(
        prefix=".startup-verify-", dir=working_dir
    ) as temporary:
        expected = clone_or_update_shared_roles(
            working_directory=temporary,
            version=version,
            repo_url=repo_url,
            repository=shared_repository(working_dir),
        )
        if not (expected / ".git").exists():
            raise click.ClickException(
                "Verification requires a Git source for the configured deployment version."
            )
        if _shared_roles_fingerprint(root) != _shared_roles_fingerprint(expected):
            raise click.ClickException(
                "Refusing to run exported roles that differ from the configured deployment version."
            )
