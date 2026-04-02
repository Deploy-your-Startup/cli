"""Wizard-style output helpers for the bootstrap flow.

Builds on the pattern established in hetzner/_output.py but adds
wizard-specific elements: progress indicators, step headers with
tracking, numbered choices, skip indicators, and summary boxes.
"""

from __future__ import annotations

import click


# ── Progress indicator ───────────────────────────────────────────────


def progress_indicator(completed: int, total: int) -> str:
    """Render a progress string like ``●●○○``.

    Args:
        completed: Number of steps already finished.
        total: Total number of steps.
    """
    return "●" * completed + "○" * (total - completed)


# ── Step headers ─────────────────────────────────────────────────────


def step_header(number: int, name: str, completed: int, total: int) -> None:
    """Print a prominent step header with progress.

    Example::

        ── Step 2 · Hetzner Cloud ──────────────────── ●●○○
    """
    progress = progress_indicator(completed, total)
    prefix = f"── Step {number} · {name} "
    padding = "─" * max(1, 52 - len(prefix))
    line = prefix + padding + " " + progress
    click.echo()
    click.echo(click.style(f"  {line}", fg="cyan", bold=True))
    click.echo()


# ── Skip indicator ───────────────────────────────────────────────────


def skip_indicator(message: str) -> None:
    """Show that a step was skipped with explanation."""
    click.echo(click.style("  ⏩ ", fg="yellow") + message)


# ── Action progress ──────────────────────────────────────────────────


def action_start(message: str) -> None:
    """Print the start of an action (``→ Doing thing...``)."""
    click.echo(click.style("  → ", fg="white", dim=True) + message)


def action_done(message: str) -> None:
    """Print a completed action (``→ Thing done ✓``)."""
    click.echo(
        click.style("  → ", fg="white", dim=True)
        + message
        + click.style(" ✓", fg="green", bold=True)
    )


def action_fail(message: str) -> None:
    """Print a failed action (``→ Thing failed ✗``)."""
    click.echo(
        click.style("  → ", fg="white", dim=True)
        + message
        + click.style(" ✗", fg="red", bold=True)
    )


# ── Numbered choice ──────────────────────────────────────────────────


def numbered_choice(prompt: str, options: list[str]) -> int:
    """Present numbered choices and return the 1-based selection.

    Args:
        prompt: The question to ask.
        options: List of option labels.

    Returns:
        1-based index of the selected option.
    """
    click.echo(f"  {prompt}")
    click.echo()
    for i, option in enumerate(options, 1):
        click.echo(f"    [{i}] {option}")
    click.echo()

    while True:
        raw = click.prompt("  Auswahl", type=str)
        try:
            choice = int(raw)
            if 1 <= choice <= len(options):
                return choice
        except ValueError:
            pass
        click.echo(click.style(f"  Bitte 1-{len(options)} eingeben.", fg="red"))


# ── Text input ───────────────────────────────────────────────────────


def text_input(label: str, **kwargs) -> str:
    """Prompt for text input with consistent indentation."""
    return click.prompt(f"  {label}", **kwargs)


def confirm(prompt: str, default: bool = True) -> bool:
    """Ask a yes/no question with consistent indentation."""
    return click.confirm(f"  {prompt}", default=default)


# ── Input summary ────────────────────────────────────────────────────


def input_summary(fields: dict[str, str]) -> None:
    """Display a summary table of collected inputs.

    Args:
        fields: Mapping of label → value.
    """
    click.echo()
    click.echo(click.style("  ── Zusammenfassung ", fg="white", bold=True) + "─" * 35)
    click.echo()
    max_label = max(len(k) for k in fields)
    for label, value in fields.items():
        click.echo(f"    {label:<{max_label}}  {value}")
    click.echo()


# ── Welcome banner ───────────────────────────────────────────────────


def banner() -> None:
    """Display the welcome banner."""
    click.echo()
    click.echo(
        click.style(
            "  ╔══════════════════════════════════════════════════════╗",
            fg="cyan",
        )
    )
    click.echo(
        click.style(
            "  ║          🚀 Deploy Your Startup — Bootstrap         ║",
            fg="cyan",
        )
    )
    click.echo(
        click.style(
            "  ╚══════════════════════════════════════════════════════╝",
            fg="cyan",
        )
    )
    click.echo()
    click.echo("  Lass uns dein neues Startup aufsetzen.")
    click.echo("  Ich führe dich durch jeden Schritt.")
    click.echo()


# ── Final summary box ────────────────────────────────────────────────


def summary_box(
    *,
    project_name: str,
    project_dir: str,
    github_url: str | None,
    domain: str,
    keychain: bool = True,
) -> None:
    """Display the framed final summary with next steps."""
    W = 56  # total width including borders

    def _pad(text: str) -> str:
        inner = W - 6
        return f"  ║  {text:<{inner}} ║"

    def _empty() -> str:
        return _pad("")

    top = f"  ╔{'═' * (W - 4)}╗"
    mid = f"  ╠{'═' * (W - 4)}╣"
    bot = f"  ╚{'═' * (W - 4)}╝"

    click.echo()
    click.echo(click.style(top, fg="green"))
    click.echo(click.style(_pad(f"✅ {project_name} ist bereit!"), fg="green"))
    click.echo(click.style(mid, fg="green"))
    click.echo(click.style(_empty(), fg="green"))
    click.echo(click.style(_pad(f"📁 {project_dir}"), fg="green"))
    if github_url:
        click.echo(click.style(_pad(f"🔗 {github_url}"), fg="green"))
    click.echo(click.style(_pad(f"🌐 {domain}"), fg="green"))
    if keychain:
        click.echo(click.style(_pad("🔑 Vault-Passwort → macOS Keychain"), fg="green"))
    click.echo(click.style(_empty(), fg="green"))
    click.echo(click.style(mid, fg="green"))
    click.echo(click.style(_empty(), fg="green"))
    click.echo(click.style(_pad("Nächste Schritte:"), fg="green"))
    click.echo(click.style(_empty(), fg="green"))
    click.echo(click.style(_pad(f"  cd {project_name}/deployment"), fg="green"))
    click.echo(click.style(_pad("  startup ansible setup"), fg="green"))
    click.echo(
        click.style(_pad("  startup ansible infrastructure   # ~5-10 min"), fg="green")
    )
    click.echo(click.style(_pad("  startup ansible deploy"), fg="green"))
    click.echo(click.style(_pad("  startup ansible kubeconfig"), fg="green"))
    click.echo(click.style(_empty(), fg="green"))
    click.echo(click.style(bot, fg="green"))
    click.echo()


# ── Simple helpers ───────────────────────────────────────────────────


def info(text: str) -> None:
    """Display an info message."""
    click.echo(click.style("  ℹ ", fg="blue", bold=True) + text)


def success(text: str) -> None:
    """Display a success message."""
    click.echo(click.style("  ✓ ", fg="green", bold=True) + text)


def error(text: str) -> None:
    """Display an error message."""
    click.echo(click.style("  ✗ ", fg="red", bold=True) + text)


def warning(text: str) -> None:
    """Display a warning message."""
    click.echo(click.style("  ! ", fg="yellow", bold=True) + text)
