"""GIVEN a pitch repository, WHEN bootstrap stores the Cloudflare secrets,
THEN they live in a production environment that only main can deploy to."""

import json
import sys

import click
import pytest

from cli.wizard.steps.pitch_finalize import save_cloudflare_secrets

REPO = "example/landing"
ENVIRONMENT = f"repos/{REPO}/environments/production"


def fake_github(tmp_path, monkeypatch, *, visibility, environments=True, policies=()):
    """Put a GitHub CLI stand-in on PATH that records argv and stdin."""
    commands = tmp_path / "commands"
    commands.mkdir()
    log = tmp_path / "calls.jsonl"
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"policies": list(policies)}))
    gh = commands / "gh"
    gh.write_text(f"""#!{sys.executable}
import json, sys
from pathlib import Path
argv = sys.argv[1:]
stdin = "" if sys.stdin.isatty() else sys.stdin.read()
with Path({str(log)!r}).open("a") as log:
    log.write(json.dumps({{"argv": argv, "stdin": stdin}}) + "\\n")
state_file = Path({str(state)!r})
state = json.loads(state_file.read_text())
if argv[:2] == ["repo", "view"]:
    print({visibility!r})
elif argv[:3] == ["api", "--method", "PUT"] and argv[3] == {ENVIRONMENT!r}:
    if not {environments!r}:
        print("HTTP 422: environments are not available", file=sys.stderr)
        sys.exit(1)
elif argv[:2] == ["api", {ENVIRONMENT + "/deployment-branch-policies"!r}]:
    print("\\n".join(state["policies"]))
elif argv[:3] == ["api", "--method", "POST"]:
    state["policies"].append("main")
    state_file.write_text(json.dumps(state))
""")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{commands}:/usr/bin:/bin")
    return lambda: [json.loads(line) for line in log.read_text().splitlines()]


def test_public_repository_gets_main_only_environment_and_fork_approval(
    tmp_path, monkeypatch
):
    # GIVEN a public repository on GitHub without a production environment.
    calls = fake_github(tmp_path, monkeypatch, visibility="PUBLIC")

    # WHEN bootstrap stores synthetic Cloudflare credentials.
    in_environment = save_cloudflare_secrets(
        tmp_path, REPO, "synthetic-token", "synthetic-account"
    )

    # THEN the environment allows only main, secrets go to it through stdin,
    # and fork pull request workflows need approval.
    assert in_environment
    recorded = calls()
    create = next(
        c for c in recorded if ENVIRONMENT in c["argv"] and "PUT" in c["argv"]
    )
    assert json.loads(create["stdin"]) == {
        "deployment_branch_policy": {
            "protected_branches": False,
            "custom_branch_policies": True,
        }
    }
    assert any(
        c["argv"][:3] == ["api", "--method", "POST"]
        and c["argv"][-4:] == ["-f", "name=main", "-f", "type=branch"]
        for c in recorded
    )
    secrets = [c for c in recorded if c["argv"][:2] == ["secret", "set"]]
    assert [(c["argv"], c["stdin"]) for c in secrets] == [
        (
            [
                "secret",
                "set",
                "CLOUDFLARE_API_TOKEN",
                "--repo",
                REPO,
                "--env",
                "production",
            ],
            "synthetic-token",
        ),
        (
            [
                "secret",
                "set",
                "CLOUDFLARE_ACCOUNT_ID",
                "--repo",
                REPO,
                "--env",
                "production",
            ],
            "synthetic-account",
        ),
    ]
    assert not any("synthetic" in arg for c in recorded for arg in c["argv"])
    assert any(
        c["argv"][-2:] == ["-f", "approval_policy=all_external_contributors"]
        and c["argv"][3].endswith("/actions/permissions/fork-pr-contributor-approval")
        for c in recorded
    )


def test_rerun_keeps_the_existing_main_policy(tmp_path, monkeypatch):
    # GIVEN a private repository whose environment already allows main.
    calls = fake_github(tmp_path, monkeypatch, visibility="PRIVATE", policies=["main"])

    # WHEN bootstrap runs the step again.
    save_cloudflare_secrets(tmp_path, REPO, "synthetic-token", "synthetic-account")

    # THEN no duplicate branch policy is added, and a private repository
    # leaves the fork approval setting alone.
    recorded = calls()
    assert not any(c["argv"][:3] == ["api", "--method", "POST"] for c in recorded)
    assert not any(
        "fork-pr-contributor-approval" in " ".join(c["argv"]) for c in recorded
    )


def test_private_free_plan_repository_falls_back_to_repository_secrets(
    tmp_path, monkeypatch
):
    # GIVEN a private repository on a plan without environments.
    calls = fake_github(tmp_path, monkeypatch, visibility="PRIVATE", environments=False)

    # WHEN bootstrap stores the Cloudflare credentials.
    in_environment = save_cloudflare_secrets(
        tmp_path, REPO, "synthetic-token", "synthetic-account"
    )

    # THEN they become repository secrets, still sent through stdin.
    assert not in_environment
    secrets = [c for c in calls() if c["argv"][:2] == ["secret", "set"]]
    assert [c["argv"][-2:] for c in secrets] == [["--repo", REPO]] * 2
    assert [c["stdin"] for c in secrets] == ["synthetic-token", "synthetic-account"]


def test_failed_secret_upload_keeps_the_value_private(tmp_path, monkeypatch):
    # GIVEN a GitHub CLI that echoes the secret and fails.
    commands = tmp_path / "commands"
    commands.mkdir()
    gh = commands / "gh"
    gh.write_text(f"""#!{sys.executable}
import sys
if sys.argv[1:3] == ["secret", "set"]:
    print(sys.stdin.read(), file=sys.stderr)
    sys.exit(1)
""")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{commands}:/usr/bin:/bin")

    # WHEN bootstrap stores the token.
    with pytest.raises(click.ClickException) as error:
        save_cloudflare_secrets(tmp_path, REPO, "synthetic-token", "synthetic-account")

    # THEN the error names the secret but never its value.
    assert "CLOUDFLARE_API_TOKEN" in str(error.value)
    assert "synthetic-token" not in str(error.value)
