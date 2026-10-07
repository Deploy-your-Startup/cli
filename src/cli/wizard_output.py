"""Wizard-style output helpers for the bootstrap flow.

Builds on the pattern established in hetzner/_output.py but adds
wizard-specific elements: progress indicators, step headers with
tracking, numbered choices, skip indicators, and summary boxes.
"""

from __future__ import annotations

import os
import shutil
import sys
import time

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
        raw = click.prompt("  Choose", type=str)
        try:
            choice = int(raw)
            if 1 <= choice <= len(options):
                return choice
        except ValueError:
            pass
        click.echo(click.style(f"  Enter a number from 1 to {len(options)}.", fg="red"))


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
    click.echo(click.style("  ── Your launch plan ", fg="white", bold=True) + "─" * 35)
    click.echo()
    max_label = max(len(k) for k in fields)
    for label, value in fields.items():
        click.echo(f"    {label:<{max_label}}  {value}")
    click.echo()


# ── Welcome banner ───────────────────────────────────────────────────


def banner(*, animate: bool = True) -> None:
    """Let the rocket write the wordmark once, then leave it stationary."""
    stream = sys.stdout
    prefix = "  >_ deploy your startup "
    motion = (
        animate
        and stream.isatty()
        and os.environ.get("TERM", "dumb") != "dumb"
        and not any(
            name in os.environ for name in ("NO_COLOR", "CI", "STARTUP_NO_ANIMATION")
        )
        and shutil.get_terminal_size().columns >= len(prefix) + 4
    )
    click.echo()
    if motion:
        try:
            stream.write("\x1b[?25l")
            stream.flush()
            # The rocket writes the wordmark: each step reveals one more
            # character behind it and slows down as it reaches the end.
            indent = len(prefix) - len(prefix.lstrip())
            for shown in range(indent, len(prefix) + 1):
                # Never wrap if the terminal shrinks mid-flight.
                width = max(0, shutil.get_terminal_size().columns - 4)
                text = prefix[: min(shown, width)]
                stream.write("\r\x1b[2K" + click.style(text, fg="cyan") + "🚀")
                stream.flush()
                time.sleep(0.02 + 0.05 * (shown / len(prefix)) ** 3)
        finally:
            stream.write("\r\x1b[2K\x1b[?25h")
            stream.flush()
    click.echo(click.style(prefix + "🚀", fg="cyan"))
    click.echo()
    click.echo("  Your idea. Your infrastructure.")
    click.echo("  A few questions, then we’ll guide you through setup.")
    click.echo()


# ── Final summary box ────────────────────────────────────────────────


def summary_box(
    *,
    project_name: str,
    project_dir: str,
    github_url: str | None,
    domain: str,
    kind: str = "fullstack",
    keychain_service: str | None = None,
    provider: str = "hetzner",
    byos_deploy_key_command: str | None = None,
) -> None:
    """Display the framed final summary with next steps."""
    W = 64  # total width including borders

    def _pad(text: str) -> str:
        inner = W - 6
        return f"  ║  {text:<{inner}} ║"

    def _emit(text: str = "") -> None:
        click.echo(click.style(_pad(text), fg="green"))

    def _empty() -> str:
        return _pad("")

    top = f"  ╔{'═' * (W - 4)}╗"
    mid = f"  ╠{'═' * (W - 4)}╣"
    bot = f"  ╚{'═' * (W - 4)}╝"

    click.echo()
    click.echo(click.style(top, fg="green"))
    click.echo(click.style(_pad(f"✅ {project_name} is configured!"), fg="green"))
    click.echo(click.style(mid, fg="green"))
    click.echo(click.style(_empty(), fg="green"))
    click.echo(click.style(_pad(f"📁 {project_dir}"), fg="green"))
    if github_url:
        click.echo(click.style(_pad(f"🔗 {github_url}"), fg="green"))
    click.echo(click.style(_pad(f"🌐 {domain}"), fg="green"))
    if keychain_service:
        click.echo(
            click.style(_pad(f"🔑 Keychain entry: {keychain_service}"), fg="green")
        )
    click.echo(click.style(_empty(), fg="green"))
    click.echo(click.style(mid, fg="green"))
    click.echo(click.style(_empty(), fg="green"))
    if kind == "pitch":
        click.echo(
            click.style(
                _pad("Build and deploy run through GitHub Actions"),
                fg="green",
            )
        )
        click.echo(click.style(_pad("(Push to main → Cloudflare Pages)."), fg="green"))
        click.echo(click.style(_empty(), fg="green"))
        click.echo(
            click.style(
                _pad(f"Custom Domain {domain} is connected"),
                fg="green",
            )
        )
        click.echo(
            click.style(
                _pad("(Cloudflare: DNS + TLS follow nameserver propagation)."),
                fg="green",
            )
        )
    else:
        if provider == "byos":
            _emit("BYOS: deploy locally to your server.")
            _emit()
            _emit("Copy the commands below to continue.")
            if byos_deploy_key_command:
                _emit("1) Copy the deploy key to the server")
            _emit("2) Then deploy locally")
        else:
            _emit("Build and deploy run through GitHub Actions")
            _emit("(Push to main → backend & deployment).")
            _emit()
            _emit("Deployment requested; verify it before calling it live.")
            if github_url:
                _emit(f"Status: {github_url}/actions")
            _emit("Then verify your domain’s DNS and HTTPS.")
    click.echo(click.style(_empty(), fg="green"))
    click.echo(click.style(bot, fg="green"))
    click.echo()
    if kind != "pitch" and provider == "byos":
        click.echo("  Copy-Paste:")
        click.echo()
        if byos_deploy_key_command:
            click.echo("  # Copy the deploy key to your server once")
            click.echo(f"  {byos_deploy_key_command}")
            click.echo()
        click.echo("  # Then deploy locally")
        click.echo(f"  cd {project_dir}/deployment")
        click.echo("  ./make.sh setup")
        click.echo("  ./make.sh infrastructure --environment production")
        click.echo("  ./make.sh deploy --environment production")
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
