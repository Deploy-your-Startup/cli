"""``startup evidence``: keep PR review screenshots out of code repositories.

Images go to one private evidence repository per Factory operator, named by
``evidence_repo`` in the Factory's ``projects.yaml``. Links pin the commit so
they render in private pull requests and never change afterwards.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import click
import yaml

PR_URL = re.compile(
    r"^https://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+)/pull/(?P<number>\d+)/?$"
)
REPO_NAME = re.compile(r"^[\w.-]+/[\w.-]+$")
PUSH_ATTEMPTS = 3


def _run(*args: str, cwd: Path | None = None) -> str:
    result = subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=False)
    if result.returncode != 0:
        raise click.ClickException(
            f"{' '.join(args[:3])} failed: {(result.stderr or result.stdout).strip()}"
        )
    return result.stdout.strip()


def registry_evidence_repo(directory: Path) -> str | None:
    """Return ``evidence_repo`` from the nearest Factory ``projects.yaml``."""
    for root in (directory.resolve(), *directory.resolve().parents):
        path = root / "projects.yaml"
        if not path.is_file() or path.is_symlink():
            continue
        try:
            data = yaml.safe_load(path.read_text())
        except yaml.YAMLError as exc:
            raise click.ClickException(f"Invalid Factory registry {path}.") from exc
        if isinstance(data, dict) and isinstance(data.get("projects"), list):
            return data.get("evidence_repo") or None
    return None


def _ensure_repository(repo: str) -> None:
    exists = subprocess.run(
        ["gh", "repo", "view", repo, "--json", "name"],
        text=True,
        capture_output=True,
        check=False,
    )
    if exists.returncode == 0:
        return
    click.echo(f"Creating private evidence repository {repo}...", err=True)
    _run(
        "gh",
        "repo",
        "create",
        repo,
        "--private",
        "--description",
        "Review evidence (screenshots) for pull requests",
    )


def _commit_and_push(checkout: Path, message: str) -> str:
    branch = _run("git", "symbolic-ref", "--short", "HEAD", cwd=checkout)
    _run("git", "add", "--all", cwd=checkout)
    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=checkout,
        capture_output=True,
        check=False,
    )
    if staged.returncode != 0:
        _run("git", "commit", "-q", "-m", message, cwd=checkout)
    for attempt in range(PUSH_ATTEMPTS):
        pushed = subprocess.run(
            ["git", "push", "-q", "origin", f"HEAD:refs/heads/{branch}"],
            cwd=checkout,
            text=True,
            capture_output=True,
            check=False,
        )
        if pushed.returncode == 0:
            return _run("git", "rev-parse", "HEAD", cwd=checkout)
        if attempt + 1 == PUSH_ATTEMPTS:
            raise click.ClickException(f"git push failed: {pushed.stderr.strip()}")
        # Another upload landed first; replay ours on top of it.
        _run("git", "pull", "-q", "--rebase", "origin", branch, cwd=checkout)
    raise AssertionError("unreachable")


@click.group()
def evidence():
    """Store PR review evidence outside the code repositories."""


@evidence.command("upload")
@click.option("--pr", "pr_url", required=True, help="GitHub pull request URL.")
@click.option(
    "--repo",
    "evidence_repo",
    help="Evidence repository OWNER/NAME (default: evidence_repo in projects.yaml).",
)
@click.argument(
    "images",
    nargs=-1,
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
def upload(pr_url: str, evidence_repo: str | None, images: tuple[Path, ...]):
    """Upload IMAGES for a pull request and print Markdown links to embed."""
    match = PR_URL.match(pr_url)
    if not match:
        raise click.ClickException(
            "--pr must look like https://github.com/OWNER/REPO/pull/NUMBER."
        )
    repo = evidence_repo or registry_evidence_repo(Path.cwd())
    if not repo:
        raise click.ClickException(
            "No evidence repository: set evidence_repo: OWNER/NAME in the "
            "Factory projects.yaml or pass --repo."
        )
    if not REPO_NAME.match(repo):
        raise click.ClickException(f"Evidence repository must be OWNER/NAME: {repo}")
    names = [image.name for image in images]
    if len(set(names)) != len(names):
        raise click.ClickException("Image file names must be unique per upload.")

    folder = f"{match['repo']}/pr-{match['number']}"
    _ensure_repository(repo)
    with tempfile.TemporaryDirectory(prefix="startup-evidence-") as tmp:
        checkout = Path(tmp) / "evidence"
        _run("gh", "repo", "clone", repo, str(checkout), "--", "-q", "--depth", "1")
        if subprocess.run(
            ["git", "rev-parse", "--verify", "-q", "HEAD"],
            cwd=checkout,
            capture_output=True,
            check=False,
        ).returncode:
            # A new repository has no commits yet; start its history on main.
            _run("git", "symbolic-ref", "HEAD", "refs/heads/main", cwd=checkout)
        target = checkout / folder
        target.mkdir(parents=True, exist_ok=True)
        for image in images:
            shutil.copyfile(image, target / image.name)
        sha = _commit_and_push(checkout, f"Add evidence for {pr_url}")

    for name in names:
        click.echo(
            f"![{name}](https://github.com/{repo}/blob/{sha}/{folder}/{name}?raw=true)"
        )
