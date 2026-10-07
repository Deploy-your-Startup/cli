"""Install the bundled coding-agent skill that teaches agents to drive this CLI."""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import click

SKILL_NAME = "deploy-your-startup"

# Agent → (directory that shows the agent is set up, user skill directory).
# Codex reads user skills from the shared ~/.agents/skills location.
AGENTS = {
    "claude": (".claude", ".claude/skills"),
    "codex": (".codex", ".agents/skills"),
    "opencode": (".config/opencode", ".config/opencode/skills"),
}


def bundled_skill() -> str:
    return (files("cli") / "agent_skills" / SKILL_NAME / "SKILL.md").read_text(
        encoding="utf-8"
    )


def _owned_by_us(target: Path) -> bool:
    """Only replace a skill directory that holds our own skill (or nothing)."""
    skill_file = target / "SKILL.md"
    if not target.exists():
        return True
    if not skill_file.is_file():
        return not any(target.iterdir())
    return f"name: {SKILL_NAME}\n" in skill_file.read_text(encoding="utf-8")


@click.group()
def skills():
    """Teach coding agents (Claude Code, Codex, OpenCode) to use this CLI."""


@skills.command("show")
def show():
    """Print the bundled agent skill (SKILL.md) to stdout."""
    click.echo(bundled_skill(), nl=False)


@skills.command("install")
@click.option(
    "--agent",
    "agents",
    type=click.Choice(sorted(AGENTS)),
    multiple=True,
    help="Agent to install for (repeatable). Default: every agent found in your home directory.",
)
@click.option("--dry-run", is_flag=True, help="Show the target paths only.")
def install(agents: tuple[str, ...], dry_run: bool):
    """Install the Deploy Your Startup skill for your coding agents.

    The skill tells the agent how to set up and operate `startup` safely:
    bootstrap, deploy, secrets and updates. Re-run it after updating the CLI
    to get the skill that matches this version.
    """
    home = Path.home()
    if not agents:
        agents = tuple(
            name for name, (marker, _) in AGENTS.items() if (home / marker).is_dir()
        )
    if not agents:
        raise click.ClickException(
            "No coding agent found in your home directory. "
            "Choose one with --agent " + "|".join(sorted(AGENTS)) + "."
        )

    content = bundled_skill()
    for name in agents:
        target = home / AGENTS[name][1] / SKILL_NAME
        if not _owned_by_us(target):
            raise click.ClickException(
                f"{target} contains a different skill. Move it, then run this again."
            )
        if dry_run:
            click.echo(f"Would install for {name}: {target / 'SKILL.md'}")
            continue
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(content, encoding="utf-8")
        click.echo(f"Installed for {name}: {target / 'SKILL.md'}")
    if not dry_run:
        click.echo("Start a new agent session, then ask it to set up your startup.")
