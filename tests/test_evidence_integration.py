"""Exercise `startup evidence upload` with real git and a fake `gh`."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PR = "https://github.com/owner/shop/pull/42"

# Stand-in for GitHub: each repository is a bare repository under TEST_REMOTES.
FAKE_GH = """import os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
remotes = Path(os.environ['TEST_REMOTES'])
with open(remotes / 'gh.log', 'a') as log:
    log.write(' '.join(args) + '\\n')
remote = remotes / (args[2].replace('/', '__') + '.git') if len(args) > 2 else None
if args[:2] == ['repo', 'view']:
    raise SystemExit(0 if remote.exists() else 1)
if args[:2] == ['repo', 'create']:
    raise SystemExit(subprocess.call(['git', 'init', '-q', '--bare', '-b', 'main', str(remote)]))
if args[:2] == ['repo', 'clone']:
    raise SystemExit(subprocess.call(['git', 'clone', *args[5:], 'file://' + str(remote), args[3]]))
raise SystemExit(2)
"""


def environment(tmp_path: Path) -> dict[str, str]:
    remotes = tmp_path / "remotes"
    remotes.mkdir()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    gh = fake_bin / "gh"
    gh.write_text("#!" + sys.executable + "\n" + FAKE_GH)
    gh.chmod(0o755)
    return {
        **os.environ,
        "PATH": str(fake_bin) + os.pathsep + os.environ["PATH"],
        "PYTHONPATH": str(ROOT / "src"),
        "TEST_REMOTES": str(remotes),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Agent",
        "GIT_AUTHOR_EMAIL": "agent@example.invalid",
        "GIT_COMMITTER_NAME": "Agent",
        "GIT_COMMITTER_EMAIL": "agent@example.invalid",
        "NO_COLOR": "1",
    }


def factory(tmp_path: Path, evidence_repo: str) -> Path:
    root = tmp_path / "factory"
    project = root / "projects/startups/shop"
    project.mkdir(parents=True)
    (root / "projects.yaml").write_text(
        f"evidence_repo: {evidence_repo}\nprojects: []\ntooling: []\n"
    )
    return project


def upload(cwd: Path, env: dict[str, str], *args: str):
    return subprocess.run(
        [sys.executable, "-m", "cli.startup", "evidence", "upload", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )


def show(env: dict[str, str], remote: Path, revision: str) -> bytes:
    return subprocess.run(
        ["git", "--git-dir", str(remote), "show", revision],
        env=env,
        capture_output=True,
        check=True,
    ).stdout


def test_first_upload_creates_private_repo_and_prints_pinned_links(tmp_path):
    # GIVEN a Factory naming an evidence repository that does not exist yet
    env = environment(tmp_path)
    project = factory(tmp_path, "operator/review-evidence")
    before = project / "before.png"
    after = project / "after.png"
    before.write_bytes(b"\x89PNG before")
    after.write_bytes(b"\x89PNG after")

    # WHEN an agent uploads screenshots from inside a startup checkout
    result = upload(project, env, "--pr", PR, "before.png", "after.png")

    # THEN the repository is created private and holds the images at a pinned commit
    assert result.returncode == 0, result.stdout + result.stderr
    log = (tmp_path / "remotes/gh.log").read_text()
    assert "repo create operator/review-evidence --private" in log
    remote = tmp_path / "remotes/operator__review-evidence.git"
    sha = subprocess.run(
        ["git", "--git-dir", str(remote), "rev-parse", "main"],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    assert show(env, remote, f"{sha}:shop/pr-42/after.png") == b"\x89PNG after"
    base = f"https://github.com/operator/review-evidence/blob/{sha}/shop/pr-42"
    assert result.stdout.splitlines() == [
        f"![before.png]({base}/before.png?raw=true)",
        f"![after.png]({base}/after.png?raw=true)",
    ]
    # AND the code repository stays untouched
    assert sorted(p.name for p in project.iterdir()) == ["after.png", "before.png"]


def test_later_upload_appends_to_existing_history(tmp_path):
    # GIVEN an evidence repository that already holds another PR's evidence
    env = environment(tmp_path)
    project = factory(tmp_path, "operator/review-evidence")
    shot = project / "shot.png"
    shot.write_bytes(b"one")
    assert upload(project, env, "--pr", PR, "shot.png").returncode == 0
    shot.write_bytes(b"two")

    # WHEN evidence for a second PR is uploaded
    result = upload(
        project, env, "--pr", "https://github.com/owner/shop/pull/43", "shot.png"
    )

    # THEN it is committed on top, without recreating the repository
    assert result.returncode == 0, result.stdout + result.stderr
    remote = tmp_path / "remotes/operator__review-evidence.git"
    assert show(env, remote, "main:shop/pr-42/shot.png") == b"one"
    assert show(env, remote, "main:shop/pr-43/shot.png") == b"two"
    assert (tmp_path / "remotes/gh.log").read_text().count("repo create") == 1


def test_missing_registry_entry_and_bad_pr_url_fail_clearly(tmp_path):
    # GIVEN a Factory without evidence_repo
    env = environment(tmp_path)
    project = factory(tmp_path, "''")
    (project / "shot.png").write_bytes(b"x")

    # WHEN uploading without --repo, or with a non-PR URL
    missing = upload(project, env, "--pr", PR, "shot.png")
    bad = upload(
        project, env, "--pr", "https://example.com/1", "--repo", "a/b", "shot.png"
    )

    # THEN both fail before touching GitHub
    assert missing.returncode != 0
    assert "evidence_repo" in missing.stderr
    assert bad.returncode != 0
    assert "--pr must look like" in bad.stderr
    assert not (tmp_path / "remotes/gh.log").exists()
