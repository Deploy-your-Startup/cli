"""Small terminal palette shared by onboarding and provider setup."""

from __future__ import annotations

import os
import sys

import click


def supports_color() -> bool:
    """Keep redirected output and plain-terminal preferences free of escapes."""
    return (
        sys.stdout.isatty()
        and os.environ.get("TERM", "dumb") != "dumb"
        and not os.environ.get("NO_COLOR")
    )


def style(
    text: str,
    *,
    fg: str | int | tuple[int, int, int] | None = None,
    bold: bool = False,
    dim: bool = False,
) -> str:
    """Style only decoration; leave the terminal's text and background intact."""
    if not supports_color():
        return text
    return click.style(text, fg=fg, bold=bold or None, dim=dim or None)


def accent(text: str) -> str:
    """Match the website/installer blue, with the same terminal fallbacks."""
    term = os.environ.get("TERM", "")
    color: int | tuple[int, int, int] | None = None
    if os.environ.get("COLORTERM") in ("truecolor", "24bit") or "direct" in term:
        color = (156, 190, 220)  # #9cbedc on the active homepage.
    elif "256color" in term:
        color = 110
    return style(text, fg=color)
