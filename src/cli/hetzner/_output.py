"""Provider prompts with the same presentation as the bootstrap wizard."""

import click

from cli import terminal_style as terminal
from cli import wizard_output as ui
from cli.wizard_output import error as error
from cli.wizard_output import info as info
from cli.wizard_output import success as success
from cli.wizard_output import warning as warning


def banner() -> None:
    """Display the CLI banner."""
    click.echo()
    ui.wordmark()
    click.echo("  Hetzner Cloud setup")
    click.echo()


def step(number: int, text: str) -> None:
    """Display a numbered step."""
    click.echo()
    click.echo(terminal.accent(f"  {number}.") + f"  {text}")


def ask(prompt_text: str, default: str | None = None, password: bool = False) -> str:
    """Ask user for input."""
    return click.prompt(f"  {prompt_text}", default=default, hide_input=password)


def confirm(prompt_text: str, default: bool = True) -> bool:
    """Ask user for confirmation."""
    return click.confirm(f"  {prompt_text}", default=default)
