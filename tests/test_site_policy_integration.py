"""Real CLI processes and rendered templates for public policy configuration."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from copier import run_copy

from cli.site_policy import check_template_policy, install_policy, read_policy

POLICY = {
    "operatorName": "Example Operator",
    "operatorAddress": "Example Street 1, 12345 Example City",
    "contactEmail": "contact@example.test",
    "additionalImprint": "",
    "privacyNotice": "Hosting, contact, rights and retention reviewed for this fixture.",
    "reviewed": True,
    "analytics": "off",
    "revision": 1,
}


def run_cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "cli.startup", *args],
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "relative",
    ["frontend/src/data/site-policy.json", "backend/project/site-policy.json"],
)
def test_doctor_validates_real_project_files(tmp_path, relative):
    # GIVEN a project with an unreviewed public policy
    path = tmp_path / relative
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({**POLICY, "reviewed": False, "operatorAddress": ""}))
    before = path.read_bytes()
    # WHEN the owner checks the project through the real CLI
    result = run_cli("doctor", "--project-dir", str(tmp_path))
    # THEN it fails clearly without changing or printing personal data
    assert result.returncode == 1
    assert "operatorAddress" in result.stderr
    assert "review" in result.stderr
    assert path.read_bytes() == before
    assert POLICY["contactEmail"] not in result.stdout + result.stderr
    # WHEN the operator completes and reviews the configuration
    path.write_text(json.dumps(POLICY))
    result = run_cli("doctor", "--project-dir", str(tmp_path))
    # THEN the configuration passes without claiming legal compliance
    assert result.returncode == 0, result.stderr
    assert "not legal completeness" in result.stdout


@pytest.mark.parametrize(
    "change",
    [
        {"revision": True},
        {"analytics": "automatic"},
        {"reviewed": "true"},
        {"contactEmail": "invalid"},
    ],
)
def test_bootstrap_rejects_invalid_policy_before_accounts(tmp_path, change):
    # GIVEN an invalid policy and no credentials
    path = tmp_path / "policy.json"
    path.write_text(json.dumps({**POLICY, **change}))
    # WHEN starting bootstrap
    result = run_cli("bootstrap", "--site-policy", str(path), "--yes")
    # THEN validation fails before account, browser or infrastructure work
    assert result.returncode == 1
    assert "Site policy incomplete" in result.stderr
    assert list(tmp_path.iterdir()) == [path]


def test_render_install_and_resume_preserve_operator_policy(tmp_path):
    # GIVEN a real minimal Copier template exposing the same policy contract
    source = tmp_path / "template"
    source.mkdir()
    (source / "copier.yml").write_text("_subdirectory: template\n")
    target = source / "template/frontend/src/data/site-policy.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {
                **POLICY,
                "reviewed": False,
                "operatorName": "",
                "operatorAddress": "",
                "contactEmail": "",
                "privacyNotice": "",
            }
        )
    )
    generated = tmp_path / "generated"
    run_copy(str(source), generated, defaults=True, skip_tasks=True)
    public_policy = tmp_path / "operator.json"
    public_policy.write_text(json.dumps(POLICY))
    # WHEN bootstrap's public configuration step installs the reviewed policy twice
    install_policy(generated, read_policy(public_policy))
    install_policy(generated, read_policy(public_policy))
    # THEN the generated application reads the same values and doctor passes
    assert (
        json.loads((generated / "frontend/src/data/site-policy.json").read_text())
        == POLICY
    )
    assert run_cli("doctor", "--project-dir", str(generated)).returncode == 0
    # WHEN a resume supplies different operator data
    with pytest.raises(Exception, match="Existing operator policy differs"):
        install_policy(generated, {**POLICY, "operatorName": "Different"})
    # THEN existing operator data remains unchanged
    assert (
        json.loads((generated / "frontend/src/data/site-policy.json").read_text())
        == POLICY
    )


def test_template_capabilities_reject_before_provider_setup(tmp_path):
    # GIVEN a real local template without policy support
    (tmp_path / "copier.yml").write_text("_subdirectory: template\n")
    (tmp_path / "template").mkdir()
    manifest = tmp_path / "startup-template.yml"
    manifest.write_text("schema: 1\n")
    # WHEN checking the bootstrap capability before credentials or provisioning
    with pytest.raises(Exception, match="does not support"):
        check_template_policy(str(tmp_path), "HEAD", POLICY)
    # THEN adding the declared contract permits off, but not unsupported analytics
    manifest.write_text("schema: 1\nsite_policy: 1\n")
    check_template_policy(str(tmp_path), "HEAD", POLICY)
    with pytest.raises(Exception, match="no analytics integration"):
        check_template_policy(str(tmp_path), "HEAD", {**POLICY, "analytics": "consent"})
