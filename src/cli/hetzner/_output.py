"""Click-based output helpers for Hetzner browser automation.

Thin wrapper around click.echo() + click.style() that provides the same
interface pattern as the prototype's Rich-based ui.py, but without adding
Rich as a dependency.
"""

import click


def banner():
    """Display the CLI banner."""
    click.echo()
    click.echo(click.style("  Hetzner Cloud Bootstrap", fg="cyan", bold=True))
    click.echo(click.style("  Interactive setup for account, project, domain & API token", fg="white", dim=True))
    click.echo()


def step(number: int, text: str):
    """Display a numbered step."""
    click.echo()
    click.echo(click.style(f"  Step {number}: ", fg="yellow", bold=True) + text)


def success(text: str):
    """Display a success message."""
    click.echo(click.style("  \u2713 ", fg="green", bold=True) + text)


def error(text: str):
    """Display an error message."""
    click.echo(click.style("  \u2717 ", fg="red", bold=True) + text)


def info(text: str):
    """Display an info message."""
    click.echo(click.style("  i ", fg="blue", bold=True) + text)


def warning(text: str):
    """Display a warning message."""
    click.echo(click.style("  ! ", fg="yellow", bold=True) + text)


def ask(prompt_text: str, default: str | None = None, password: bool = False) -> str:
    """Ask user for input."""
    return click.prompt(f"  {prompt_text}", default=default, hide_input=password)


def confirm(prompt_text: str, default: bool = True) -> bool:
    """Ask user for confirmation."""
    return click.confirm(f"  {prompt_text}", default=default)
