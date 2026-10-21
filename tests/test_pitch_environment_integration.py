"""GIVEN a pitch repository, WHEN bootstrap stores the Cloudflare secrets,
THEN a public repository keeps them in a production environment limited to main
and a private one keeps repository secrets, as GitHub's free plan allows."""

import json
import sys

import click
import pytest

from cli.wizard.steps.pitch_finalize import (
    public_repository_lacks_environment,
    save_cloudflare_secrets,
)

REPO = "example/landing"
ENVIRONMENT = f"repos/{REPO}/environments/production"
ENV_SECRETS = ["--repo", REPO, "--env", "production"]


def fake_github(
    tmp_path, monkeypatch, *, visibility, environment=False, policies=(), secrets=()
):
    """Put a GitHub CLI stand-in on PATH that records argv and stdin."""
    commands = tmp_path / "commands"
    commands.mkdir()
    log = tmp_path / "calls.jsonl"
    state = tmp_path / "state.json"
    state.write_text(
        json.dumps(
            {
                "environment": environment,
                "policies": list(policies),
                "secrets": list(secrets),
            }
        )
    )
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
elif argv == ["api", {ENVIRONMENT!r}]:
    sys.exit(0 if state["environment"] else 1)
elif argv[:4] == ["api", "--method", "PUT", {ENVIRONMENT!r}]:
    state["environment"] = True
elif argv[:2] == ["api", {ENVIRONMENT + "/deployment-branch-policies"!r}]:
    print("\\n".join(state["policies"]))
elif argv[:3] == ["api", "--method", "POST"]:
    state["policies"].append("main")
elif argv[:2] == ["secret", "set"] and "--env" not in argv:
    state["secrets"].append(argv[2])
elif argv[:2] == ["secret", "list"]:
    print("\\n".join(state["secrets"]))
elif argv[:2] == ["secret", "delete"]:
    state["secrets"].remove(argv[2])
state_file.write_text(json.dumps(state))
""")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{commands}:/usr/bin:/bin")

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()]

    def current():
        return json.loads(state.read_text())

    return calls, current


def test_private_repository_keeps_repository_secrets(tmp_path, monkeypatch):
    # GIVEN a private repository on GitHub's free plan.
    calls, state = fake_github(tmp_path, monkeypatch, visibility="PRIVATE")

    # WHEN bootstrap stores synthetic Cloudflare credentials.
    in_environment = save_cloudflare_secrets(
        tmp_path, REPO, "synthetic-token", "synthetic-account"
    )

    # THEN they become repository secrets sent through stdin, and bootstrap
    # neither creates an environment nor touches fork approval.
    assert not in_environment
    recorded = calls()
    secrets = [c for c in recorded if c["argv"][:2] == ["secret", "set"]]
    assert [(c["argv"][2:], c["stdin"]) for c in secrets] == [
        (["CLOUDFLARE_API_TOKEN", "--repo", REPO], "synthetic-token"),
        (["CLOUDFLARE_ACCOUNT_ID", "--repo", REPO], "synthetic-account"),
    ]
    assert not any(c["argv"][0] == "api" for c in recorded)
    assert not state()["environment"]
    assert not public_repository_lacks_environment(REPO)


def test_public_repository_moves_secrets_into_main_only_environment(
    tmp_path, monkeypatch
):
    # GIVEN a repository made public after bootstrap stored repository secrets.
    calls, state = fake_github(
        tmp_path,
        monkeypatch,
        visibility="PUBLIC",
        secrets=["CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "OTHER_SECRET"],
    )
    assert public_repository_lacks_environment(REPO)

    # WHEN bootstrap runs again.
    in_environment = save_cloudflare_secrets(
        tmp_path, REPO, "synthetic-token", "synthetic-account"
    )

    # THEN the environment allows only main and holds the secrets, the
    # repository copies are gone and fork PR workflows need approval.
    assert in_environment
    recorded = calls()
    create = next(
        c for c in recorded if c["argv"][:4] == ["api", "--method", "PUT", ENVIRONMENT]
    )
    assert json.loads(create["stdin"]) == {
        "deployment_branch_policy": {
            "protected_branches": False,
            "custom_branch_policies": True,
        }
    }
    assert state()["policies"] == ["main"]
    secrets = [c for c in recorded if c["argv"][:2] == ["secret", "set"]]
    assert [(c["argv"][2:], c["stdin"]) for c in secrets] == [
        (["CLOUDFLARE_API_TOKEN", *ENV_SECRETS], "synthetic-token"),
        (["CLOUDFLARE_ACCOUNT_ID", *ENV_SECRETS], "synthetic-account"),
    ]
    assert state()["secrets"] == ["OTHER_SECRET"]
    assert not any("synthetic" in arg for c in recorded for arg in c["argv"])
    assert any(
        c["argv"][-2:] == ["-f", "approval_policy=all_external_contributors"]
        and c["argv"][3].endswith("/actions/permissions/fork-pr-contributor-approval")
        for c in recorded
    )
    assert not public_repository_lacks_environment(REPO)


def test_rerun_keeps_the_existing_main_policy(tmp_path, monkeypatch):
    # GIVEN a public repository whose environment already allows main.
    calls, _ = fake_github(
        tmp_path, monkeypatch, visibility="PUBLIC", environment=True, policies=["main"]
    )

    # WHEN bootstrap runs the step again.
    save_cloudflare_secrets(tmp_path, REPO, "synthetic-token", "synthetic-account")

    # THEN no duplicate branch policy is added.
    assert not any(c["argv"][:3] == ["api", "--method", "POST"] for c in calls())


def test_failed_secret_upload_keeps_the_value_private(tmp_path, monkeypatch):
    # GIVEN a GitHub CLI that echoes the secret and fails.
    commands = tmp_path / "commands"
    commands.mkdir()
    gh = commands / "gh"
    gh.write_text(f"""#!{sys.executable}
import sys
if sys.argv[1:3] == ["repo", "view"]:
    print("PRIVATE")
elif sys.argv[1:3] == ["secret", "set"]:
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
