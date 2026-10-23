"""Per-computer settings of the CLI, kept in ``~/.config/startup/config.toml``.

The file holds choices, never secrets: which backend keeps vault passwords and,
for 1Password, which vault and account. Every project on the computer shares
it, so a setting made once also applies to coding agents running the CLI.

``STARTUP_CONFIG_HOME`` replaces the directory (tests, several setups);
otherwise ``XDG_CONFIG_HOME`` is honored before ``~/.config``.
"""

from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

import click

# The settings the CLI reads, per table. Anything else in the file is ignored.
KNOWN_KEYS = {"vault": ("backend", "op_vault", "op_account")}


def config_path() -> Path:
    if directory := os.environ.get("STARTUP_CONFIG_HOME"):
        return Path(directory) / "config.toml"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "startup" / "config.toml"


def load() -> dict[str, dict[str, str]]:
    """Return the known settings; a missing file means no settings."""
    path = config_path()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise click.ClickException(
            f"Could not read {path}: {error}. Fix or delete the file."
        ) from None
    return {
        table: {key: str(value) for key, value in data[table].items() if key in keys}
        for table, keys in KNOWN_KEYS.items()
        if isinstance(data.get(table), dict)
    }


def get(table: str, key: str) -> str | None:
    return load().get(table, {}).get(key) or None


def update(table: str, values: dict[str, str | None]) -> Path:
    """Set (or, with ``None``, remove) keys of one table and save the file."""
    settings = load()
    section = settings.setdefault(table, {})
    for key, value in values.items():
        if value is None:
            section.pop(key, None)
        else:
            section[key] = value
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for name, entries in settings.items():
        if not entries:
            continue
        lines.append(f"[{name}]")
        # A JSON string is a valid TOML basic string.
        lines.extend(f"{key} = {json.dumps(value)}" for key, value in entries.items())
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
