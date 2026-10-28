"""Directory input with terminal completion and a plain-stream fallback."""

from __future__ import annotations

import os
import sys
from contextlib import contextmanager

import click


def _matches(value: str, file_okay: bool) -> list[str]:
    directory, prefix = os.path.split(value)
    try:
        with os.scandir(os.path.expanduser(directory or ".")) as entries:
            return sorted(
                os.path.join(directory, entry.name) + (os.sep if entry.is_dir() else "")
                for entry in entries
                if entry.name.startswith(prefix)
                and (file_okay or entry.is_dir())
                and (prefix.startswith(".") or not entry.name.startswith("."))
            )
    except OSError:
        return []


@contextmanager
def _completion(readline, path_type: click.Path):
    previous = readline.get_completer()
    delimiters = readline.get_completer_delims()

    def complete(value, index):
        matches = _matches(value, path_type.file_okay)
        return matches[index] if index < len(matches) else None

    readline.set_completer(complete)
    # Treat the entire path, including spaces, as one completion.
    readline.set_completer_delims("")
    libedit = "libedit" in (readline.__doc__ or "")
    readline.parse_and_bind("bind ^I rl_complete" if libedit else "tab: complete")
    try:
        yield
    finally:
        readline.set_completer(previous)
        readline.set_completer_delims(delimiters)
        readline.parse_and_bind("bind ^I ed-insert" if libedit else "tab: self-insert")


def _terminal_readline(hidden: bool):
    if hidden or not sys.stdin.isatty() or not sys.stdout.isatty():
        return None
    try:
        import readline
    except ImportError:
        return None
    readline.set_auto_history(False)
    return readline


def prompt(text: str, **kwargs):
    path_type = kwargs.get("type")
    if isinstance(path_type, click.Path):
        kwargs["value_proc"] = lambda value: path_type.convert(
            os.path.expanduser(value), None, None
        )
    readline = _terminal_readline(bool(kwargs.get("hide_input")))
    if readline is not None and isinstance(path_type, click.Path):
        with _completion(readline, path_type):
            return click.prompt(f"  {text.strip()} (Tab to complete)", **kwargs)
    return click.prompt(f"  {text.strip()}", **kwargs)
