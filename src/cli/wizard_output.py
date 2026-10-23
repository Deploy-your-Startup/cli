"""Compact output for bootstrap and provider setup."""

from __future__ import annotations

import os
import shutil
import sys
import time

import click

from cli import terminal_style as terminal

# ── Step headers ─────────────────────────────────────────────────────


def step_header(number: int, name: str, total: int) -> None:
    """Print the step's position and name on one short line."""
    click.echo()
    click.echo(terminal.accent(f"  {number}/{total}") + f"  {name}")


# ── Skip indicator ───────────────────────────────────────────────────


def skip_indicator(message: str) -> None:
    """Show that a step was skipped with explanation."""
    click.echo(terminal.style("  · ", dim=True) + message)


# ── Action progress ──────────────────────────────────────────────────


def action_start(message: str) -> None:
    """Print an action without interfering with its detailed logs."""
    click.echo(terminal.accent("  · ") + message)


def action_done(message: str) -> None:
    """Print a completed action with the shared success marker."""
    success(message)


def action_fail(message: str) -> None:
    """Print a failed action with the shared error marker."""
    error(message)


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
    for i, option in enumerate(options, 1):
        click.echo(terminal.accent(f"    {i}.") + f" {option}")

    while True:
        raw = click.prompt("  Choose", type=str)
        try:
            choice = int(raw)
            if 1 <= choice <= len(options):
                return choice
        except ValueError:
            pass
        error(f"Enter a number from 1 to {len(options)}.")


# ── Text input ───────────────────────────────────────────────────────


def text_input(label: str, **kwargs) -> str:
    """Prompt for text input with consistent indentation."""
    if not kwargs.get("hide_input") and sys.stdin.isatty() and sys.stdout.isatty():
        try:
            # Python scripts do not load readline automatically. Without it,
            # input() relies on the terminal's single configured erase byte,
            # so a different Backspace code becomes part of the project name.
            import readline
        except ImportError:
            # Keep basic prompts available on Python builds without readline.
            pass
        else:
            readline.set_auto_history(False)
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
    click.echo(terminal.style("  Your launch plan", bold=True))
    _fields(fields)
    click.echo()


def _fields(fields: dict[str, str]) -> None:
    max_label = max((len(k) for k in fields), default=0)
    for label, value in fields.items():
        click.echo(f"    {label:<{max_label}}  {value}")


# ── Welcome banner ───────────────────────────────────────────────────


def wordmark() -> None:
    """Use the installer's compact wordmark in each entry flow."""
    click.echo(
        terminal.accent("  >_") + terminal.style(" deploy your startup", bold=True)
    )


def banner(*, animate: bool = True) -> None:
    """Reveal the brand once, then leave a static, compact introduction."""
    stream = sys.stdout
    prefix = "  >_ deploy your startup "
    motion = (
        animate
        and terminal.supports_color()
        and not os.environ.get("CI")
        and not os.environ.get("STARTUP_NO_ANIMATION")
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
                stream.write("\r\x1b[2K" + terminal.accent(text) + "🚀")
                stream.flush()
                time.sleep(0.02 + 0.05 * (shown / len(prefix)) ** 3)
        finally:
            stream.write("\r\x1b[2K\x1b[?25h")
            stream.flush()
    wordmark()
    click.echo("  Ideas in. Startups out.")
    click.echo()


# ── Final summary ────────────────────────────────────────────────


def summary_box(
    *,
    project_name: str,
    project_dir: str,
    github_url: str | None,
    domain: str,
    kind: str = "fullstack",
    vault_password_location: str | None = None,
    provider: str = "hetzner",
    cluster_owner: str | None = None,
) -> None:
    """Show the configured project and its links without a second setup checklist."""
    click.echo(terminal.style("  Setup complete", bold=True))
    success(f"{project_name} is configured!")
    click.echo()
    fields = {"Project folder": project_dir, "Website": f"https://{domain}"}
    if github_url:
        fields["GitHub"] = github_url
        fields["GitHub Actions"] = f"{github_url}/actions"
    if kind == "pitch":
        fields["Hosting"] = "Cloudflare Pages"
    elif cluster_owner:
        fields["Cluster owner"] = cluster_owner
    else:
        fields["Server"] = (
            "Your existing server (BYOS)" if provider == "byos" else "Hetzner Cloud"
        )
    if vault_password_location:
        fields["Vault password"] = f"Saved in {vault_password_location}"
    if github_url:
        fields["Deployment"] = "Requested via GitHub Actions"
    fields["Website status"] = "Not verified yet"
    _fields(fields)
    click.echo()


# ── Simple helpers ───────────────────────────────────────────────────


def info(text: str) -> None:
    """Display an info message."""
    click.echo(terminal.accent("  · ") + text)


def success(text: str) -> None:
    """Display a success message."""
    click.echo(terminal.style("  ✓ ", fg="green", bold=True) + text)


def error(text: str) -> None:
    """Display an error message."""
    click.echo(terminal.style("  ✗ ", fg="red", bold=True) + text)


def warning(text: str) -> None:
    """Display a warning message."""
    click.echo(terminal.style("  ! ", fg="yellow", bold=True) + text)
