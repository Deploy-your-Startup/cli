"""Validate public website disclosures without claiming legal compliance."""

import json
from pathlib import Path

import click

POLICY_PATHS = (
    Path("frontend/src/data/site-policy.json"),
    Path("backend/project/site-policy.json"),
)
REQUIRED_TEXT = ("operatorName", "operatorAddress", "contactEmail", "privacyNotice")


def read_policy(path: Path) -> dict:
    try:
        policy = json.loads(path.read_text())
    except (OSError, ValueError) as exc:
        raise click.ClickException(f"Cannot read site policy: {path}") from exc
    if not isinstance(policy, dict):
        raise click.ClickException("Site policy must be a JSON object.")
    errors = policy_errors(policy)
    if errors:
        raise click.ClickException("Site policy incomplete: " + "; ".join(errors))
    return policy


def policy_errors(policy: dict) -> list[str]:
    errors = []
    for field in REQUIRED_TEXT:
        value = policy.get(field)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"missing {field}")
    email = policy.get("contactEmail", "")
    if isinstance(email, str) and email and ("@" not in email or "\n" in email):
        errors.append("invalid contactEmail")
    if policy.get("reviewed") is not True:
        errors.append("operator review is pending (reviewed must be true)")
    if policy.get("analytics") not in ("off", "consent"):
        errors.append("analytics must be off or consent")
    if type(policy.get("revision")) is not int or policy["revision"] < 1:
        errors.append("revision must be a positive integer")
    return errors


def install_policy(project: Path, policy: dict) -> None:
    paths = [
        project / relative
        for relative in POLICY_PATHS
        if (project / relative).is_file()
    ]
    if not paths:
        raise click.ClickException("This template does not support --site-policy.")
    for path in paths:
        existing = json.loads(path.read_text())
        if existing == policy:
            continue
        if existing.get("reviewed") is True or any(
            existing.get(key) for key in REQUIRED_TEXT
        ):
            raise click.ClickException(
                "Existing operator policy differs; edit and review it explicitly."
            )
        if path == project / POLICY_PATHS[1] and policy["analytics"] != "off":
            raise click.ClickException(
                "The Django template has no analytics integration; use analytics=off."
            )
        path.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n")


def check_project_policy(project: Path) -> None:
    paths = [
        project / relative
        for relative in POLICY_PATHS
        if (project / relative).is_file()
    ]
    if not paths:
        raise click.ClickException(
            "No site-policy.json found; update the application template first."
        )
    for path in paths:
        policy = read_policy(path)
        if path == project / POLICY_PATHS[1] and policy["analytics"] != "off":
            raise click.ClickException(
                "Django template analytics must be off; no tracker integration exists."
            )
    click.echo(
        "Site policy checks passed. This checks configuration, not legal completeness or live tracking."
    )


def check_template_policy(source: str, version: str, policy: dict) -> None:
    import yaml
    from copier.main import Worker

    with Worker(
        src_path=source, vcs_ref=version, skip_tasks=True, quiet=True
    ) as worker:
        manifest = worker.template.local_abspath / "startup-template.yml"
        values = yaml.safe_load(manifest.read_text()) if manifest.is_file() else {}
        if not isinstance(values, dict) or values.get("site_policy") != 1:
            raise click.ClickException(
                "This template revision does not support --site-policy."
            )
        if policy["analytics"] == "consent" and values.get("kpis") is not True:
            raise click.ClickException(
                "This template has no analytics integration; use analytics=off."
            )
