"""GIVEN local remotes, WHEN a source ref is synced, THEN target main gets that ref."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src"


def git(*args, cwd, env):
    return subprocess.run(
        ["git", *args], cwd=cwd, env=env, text=True, capture_output=True, check=True
    ).stdout.strip()


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("ref_kind", ["branch", "tag", "commit"])
def test_sync_uses_requested_source_ref_and_keeps_target_main(
    tmp_path, dry_run, ref_kind
):
    # GIVEN source and target bare git remotes behind a fake GitHub boundary.
    env = {
        **os.environ,
        "PYTHONPATH": str(SOURCE),
        "GIT_AUTHOR_NAME": "Integration",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Integration",
        "GIT_COMMITTER_EMAIL": "test@example.com",
        "TEST_REMOTES": str(tmp_path),
    }
    for repo in ["deploy-template", "deploy-your-startup"]:
        checkout = tmp_path / (repo + "-checkout")
        checkout.mkdir()
        git("init", "-b", "main", cwd=checkout, env=env)
        (checkout / "role.txt").write_text("old")
        git("add", ".", cwd=checkout, env=env)
        git("commit", "-m", "initial", cwd=checkout, env=env)
        if repo == "deploy-template":
            git("checkout", "-b", "codex/tested-roles", cwd=checkout, env=env)
            (checkout / "role.txt").write_text(
                "new for §§deploy_your_startup.github_username§§"
            )
            git("add", ".", cwd=checkout, env=env)
            git("commit", "-m", "new roles", cwd=checkout, env=env)
            source_ref = "codex/tested-roles"
            if ref_kind == "tag":
                git("tag", "v0.1.0", cwd=checkout, env=env)
                source_ref = "v0.1.0"
            elif ref_kind == "commit":
                source_ref = git("rev-parse", "HEAD", cwd=checkout, env=env)
            if ref_kind != "branch":
                (checkout / "role.txt").write_text("unreviewed future roles")
                git("add", ".", cwd=checkout, env=env)
                git("commit", "-m", "future roles", cwd=checkout, env=env)
        git(
            "clone",
            "--bare",
            str(checkout),
            str(tmp_path / (repo + ".git")),
            cwd=tmp_path,
            env=env,
        )
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    gh = fake_bin / "gh"
    gh.write_text(
        "#!"
        + sys.executable
        + "\n"
        + """import os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
if args[:2] == ['repo', 'clone']:
    remote = Path(os.environ['TEST_REMOTES']) / (args[2].split('/')[-1] + '.git')
    extra = args[5:] if len(args) > 4 else []
    raise SystemExit(subprocess.call(['git', 'clone', *extra, str(remote), args[3]]))
if args[:1] == ['api'] and args[1].startswith('users/'):
    print('User')
"""
    )
    gh.chmod(0o755)
    env["PATH"] = str(fake_bin) + os.pathsep + env["PATH"]
    target = tmp_path / "deploy-your-startup.git"
    before = git("rev-parse", "main", cwd=target, env=env)
    # WHEN the real CLI syncs a requested source branch using real git.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "sync",
            "--owner",
            "sample",
            "--source-version",
            source_ref,
            *(["--dry-run"] if dry_run else []),
        ],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    # THEN dry-run preserves the remote and a real sync commits selected content to main.
    assert result.returncode == 0, result.stdout + result.stderr
    content = git("show", "main:role.txt", cwd=target, env=env)
    assert content == ("old" if dry_run else "new for sample")
    assert (git("rev-parse", "main", cwd=target, env=env) == before) is dry_run
