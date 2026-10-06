"""Exercise the public onboarding CLI without creating external resources."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def launch(tmp_path, answers, *options):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "bootstrap",
            "--kind",
            "fullstack",
            "--provider",
            "hetzner",
            "--domain-owned",
            "--github-username",
            "example-owner",
            "--output-dir",
            str(tmp_path / "projects"),
            *options,
        ],
        input=answers,
        text=True,
        capture_output=True,
        check=False,
        cwd=tmp_path,
        env={
            **os.environ,
            "PYTHONPATH": str(ROOT / "src"),
            "SENTRY_DSN": "",
            "NO_COLOR": "1",
        },
        timeout=30,
    )


def test_first_launch_is_short_and_cancel_creates_nothing(tmp_path):
    # GIVEN a new user with a project name and domain, using default options.
    # WHEN they press Enter at the final confirmation.
    result = launch(tmp_path, "my-startup\nexample.com\n\n\n")
    # THEN the CLI presents an English plan and cancels without side effects.
    assert result.returncode == 0, result.stderr
    assert "deploy your startup" in result.stdout
    assert ">_" in result.stdout
    assert "Your launch plan" in result.stdout
    assert "example-owner/my-startup" in result.stdout
    assert "provider charges" in result.stdout
    assert "Cancelled. No resources were created." in result.stdout
    assert "Sentry DSN" not in result.stdout
    assert "Extra domains (comma-separated" not in result.stdout
    assert not (tmp_path / "projects").exists()
    assert "Projekt" not in result.stdout


def test_optional_settings_and_invalid_name_are_guided(tmp_path):
    # GIVEN a typo in the project name and a user who wants advanced settings.
    # WHEN they correct it, enter optional domains, then decline creation.
    result = launch(
        tmp_path, "Bad Name\nmy-startup\nexample.com\ny\nwww.example.com\n\nn\n"
    )
    # THEN retry guidance and optional prompts are understandable in English.
    assert result.returncode == 0, result.stderr
    assert "Use lowercase letters, numbers and hyphens" in result.stdout
    assert "Extra domains (comma-separated, Enter to skip)" in result.stdout
    assert "Sentry DSN (Enter to skip)" in result.stdout
    assert "Cancelled. No resources were created." in result.stdout
    assert not (tmp_path / "projects").exists()


def test_interrupted_onboarding_does_not_create_resources(tmp_path):
    # GIVEN a user who closes the input before answering the questions.
    # WHEN stdin ends during project-name entry.
    result = launch(tmp_path, "")
    # THEN the process exits with an understandable abort and no project.
    assert result.returncode != 0
    assert "Aborted" in result.stderr
    assert not (tmp_path / "projects").exists()


def test_launch_plan_shows_the_pinned_default_template(tmp_path):
    # GIVEN a new user who does not choose a template version.
    # WHEN they review the launch plan and cancel.
    result = launch(tmp_path, "my-startup\nexample.com\n\n\n")
    # THEN the plan names the tested template release this CLI pins.
    assert result.returncode == 0, result.stderr
    assert "Template  Django/FastAPI v0.1.0" in result.stdout


def test_explicit_or_custom_templates_keep_their_version(tmp_path):
    # GIVEN a user who picks a template version, or a custom template.
    # WHEN they review the launch plan and cancel.
    pinned = launch(
        tmp_path, "my-startup\nexample.com\n\n\n", "--template-version", "main"
    )
    custom = launch(
        tmp_path,
        "my-startup\nexample.com\n\n\n",
        "--template",
        "https://example.com/acme/vue-template.git",
    )
    # THEN their choice wins, and a custom template follows its HEAD.
    assert "Template  Django/FastAPI main" in pinned.stdout, pinned.stderr
    assert "Template  vue-template HEAD" in custom.stdout, custom.stderr
