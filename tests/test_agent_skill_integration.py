"""Install the bundled agent skill through the real CLI into a throwaway home."""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_SKILL = ROOT / "skills" / "deploy-your-startup" / "SKILL.md"


def startup(home, *args):
    return subprocess.run(
        [sys.executable, "-m", "cli.startup", "skills", *args],
        text=True,
        capture_output=True,
        check=False,
        env={
            **os.environ,
            "HOME": str(home),
            "PYTHONPATH": str(ROOT / "src"),
            "NO_COLOR": "1",
        },
        timeout=30,
    )


def test_install_targets_every_agent_found_in_home(tmp_path):
    # GIVEN a home directory where Claude Code and Codex are set up.
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".codex").mkdir()
    # WHEN the user installs the skill without choosing an agent.
    result = startup(tmp_path, "install")
    # THEN both agents get the published skill and OpenCode is left alone.
    assert result.returncode == 0, result.stderr
    claude = tmp_path / ".claude/skills/deploy-your-startup/SKILL.md"
    codex = tmp_path / ".agents/skills/deploy-your-startup/SKILL.md"
    assert claude.read_text() == PUBLIC_SKILL.read_text()
    assert codex.read_text() == PUBLIC_SKILL.read_text()
    assert not (tmp_path / ".config").exists()
    assert "Installed for claude" in result.stdout
    assert "Installed for codex" in result.stdout


def test_reinstall_updates_skill_and_dry_run_writes_nothing(tmp_path):
    # GIVEN an outdated copy of our skill.
    target = tmp_path / ".claude/skills/deploy-your-startup"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("---\nname: deploy-your-startup\n---\nold\n")
    # WHEN the user previews and then installs for Claude Code explicitly.
    preview = startup(tmp_path, "install", "--agent", "claude", "--dry-run")
    unchanged = (target / "SKILL.md").read_text()
    result = startup(tmp_path, "install", "--agent", "claude")
    # THEN the preview changes nothing and the install replaces the old copy.
    assert preview.returncode == 0, preview.stderr
    assert "Would install for claude" in preview.stdout
    assert unchanged.endswith("old\n")
    assert result.returncode == 0, result.stderr
    assert (target / "SKILL.md").read_text() == PUBLIC_SKILL.read_text()


def test_foreign_skill_with_same_name_is_preserved(tmp_path):
    # GIVEN someone else's skill in the target directory.
    target = tmp_path / ".claude/skills/deploy-your-startup"
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text("---\nname: something-else\n---\nmine\n")
    # WHEN the skill is installed.
    result = startup(tmp_path, "install", "--agent", "claude")
    # THEN the CLI refuses and leaves the existing file untouched.
    assert result.returncode != 0
    assert "contains a different skill" in result.stderr
    assert (target / "SKILL.md").read_text().endswith("mine\n")


def test_without_agents_the_user_is_told_how_to_choose(tmp_path):
    # GIVEN a home directory without any coding agent.
    # WHEN the user installs the skill.
    result = startup(tmp_path, "install")
    # THEN nothing is written and the error names the --agent option.
    assert result.returncode != 0
    assert "--agent claude|codex|opencode" in result.stderr
    assert list(tmp_path.iterdir()) == []


def test_published_skill_and_plugin_manifests_match_the_cli(tmp_path):
    # GIVEN the skill published for skill installers and the Claude plugin.
    # WHEN the CLI prints its bundled copy.
    result = startup(tmp_path, "show")
    manifest = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
    # THEN all channels ship the same skill, and every command it names exists.
    assert result.returncode == 0, result.stderr
    assert result.stdout == PUBLIC_SKILL.read_text()
    assert result.stdout.startswith("---\nname: deploy-your-startup\ndescription: ")
    assert manifest["plugins"][0]["name"] == plugin["name"] == "deploy-your-startup"
    assert manifest["plugins"][0]["source"] == "./"
    for command in (
        ["bootstrap"],
        ["doctor"],
        ["sync"],
        ["template", "update"],
        ["template", "adopt"],
        ["secrets", "get-field"],
        ["secrets", "update"],
        ["ansible", "deploy"],
        ["ansible", "infrastructure"],
        ["ansible", "kubeconfig"],
        ["ansible", "validate"],
        ["ansible", "update-vms"],
        ["ansible", "k3s-upgrade"],
        ["ansible", "cert-manager-upgrade"],
    ):
        assert f"startup {' '.join(command)}" in result.stdout
        help_run = subprocess.run(
            [sys.executable, "-m", "cli.startup", *command, "--help"],
            capture_output=True,
            text=True,
            check=False,
            env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
            timeout=30,
        )
        assert help_run.returncode == 0, (command, help_run.stderr)
    for option in (
        "--hetzner-token-stdin",
        "--without-auth",
        "--domain-owned",
        "--field-stdin",
        "--create-in",
    ):
        assert option in result.stdout
