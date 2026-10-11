"""Real resource files, wizard entrypoints and a local Hetzner HTTP boundary."""

import json
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import Any

import click
import pytest
from click.testing import CliRunner

from cli.wizard.context import BootstrapContext
from cli.wizard.runner import steps_for
from cli.wizard.steps.auth0 import Auth0Step
from cli.wizard.steps.cost import CostStep
from cli.wizard.steps.finalize import FinalizeStep
from cli.wizard.steps.project import ProjectStep


def price(net, gross, location="fsn1"):
    return {"location": location, "price_monthly": {"net": net, "gross": gross}}


@pytest.fixture
def provider(monkeypatch):
    state: dict[str, Any] = {
        "status": 200,
        "requests": [],
        "body": {
            "pricing": {
                "currency": "EUR",
                "vat_rate": "19.00",
                "server_types": [
                    {
                        "name": "cx23",
                        "prices": [
                            price("3.00", "3.57"),
                            price("4.00", "4.76", "nbg1"),
                        ],
                    },
                    {
                        "name": "cax11",
                        "prices": [
                            price("4.00", "4.76"),
                            price("5.00", "5.95", "nbg1"),
                        ],
                    },
                ],
                "load_balancer_types": [
                    {"name": "lb11", "prices": [price("6.00", "7.14")]}
                ],
                "primary_ips": [
                    {
                        "type": "ipv4",
                        "prices": [
                            price("1.00", "1.19"),
                            price("1.00", "1.19", "nbg1"),
                        ],
                    }
                ],
            }
        },
    }

    class Provider(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"].append((self.path, self.headers.get("Authorization")))
            self.send_response(state["status"])
            self.end_headers()
            self.wfile.write(json.dumps(state["body"]).encode())

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("HCLOUD_API_URL", f"http://127.0.0.1:{server.server_port}/v1")
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def project(tmp_path, production="", **options):
    ctx = BootstrapContext(
        project_name="sample",
        base_domain="example.com",
        additional_domains="",
        github_username="sample-owner",
        postgres_version="18.6",
        sentry_dsn="",
        output_dir=tmp_path,
        mode="local",
        hetzner_token="synthetic-token",
        **options,
    )
    variables = ctx.deployment_dir / "group_vars"
    variables.mkdir(parents=True)
    (variables / "all.yml").write_text(
        "create_load_balancer: true\nsecret: !vault |\n  $ANSIBLE_VAULT;1.1;AES256\n  123456\n"
    )
    (variables / "production.yml").write_text(production)
    return ctx


def run_step(ctx, answers=""):
    @click.command()
    def preview():
        CostStep().run(ctx)

    return CliRunner().invoke(preview, input=answers)


def test_default_resources_and_default_no(tmp_path, provider):
    # GIVEN real deployment group_vars with a Vault scalar and live HTTP prices.
    ctx = project(tmp_path)
    original = (ctx.deployment_dir / "group_vars/all.yml").read_bytes()
    # WHEN the user accepts the default No at the real wizard step prompt.
    result = run_step(ctx, "\n")
    # THEN all monthly items are shown and provisioning is cancelled.
    assert result.exit_code == 1, result.output
    assert "1 × cx23 server (fsn1)" in result.output
    assert "1 × primary IPv4" in result.output
    assert "1 × lb11 load balancer" in result.output
    assert "10.00" in result.output and "11.90" in result.output
    assert "about 11.90 EUR/month? [y/N]" in result.output
    assert (
        "Cancelled before provisioning; nothing was created in Hetzner."
        in result.output
    )
    assert "Hetzner bills hourly up to this monthly cap" in result.output
    assert provider["requests"] == [("/v1/pricing", "Bearer synthetic-token")]
    assert (ctx.deployment_dir / "group_vars/all.yml").read_bytes() == original
    assert CostStep().check(ctx) is False


@pytest.mark.parametrize("answers,non_interactive", [("y\n", False), ("", True)])
def test_overrides_precedence_and_datacenter_prices(
    tmp_path, provider, answers, non_interactive
):
    # GIVEN master/worker overrides in production, overriding all.yml defaults.
    ctx = project(
        tmp_path,
        "master_server_type: cax11\nworker_count: 2\nlocation: nbg1\ncreate_load_balancer: false\n",
        non_interactive=non_interactive,
    )
    ips = provider["body"]["pricing"]["primary_ips"][0]["prices"]
    for entry in ips:
        entry["datacenter"] = entry.pop("location") + "-dc14"
    # WHEN the real step retrieves datacenter-keyed prices and consent is given.
    result = run_step(ctx, answers)
    # THEN servers and IPv4s are multiplied accurately; disabled LB is excluded.
    assert result.exit_code == 0, result.output
    assert "1 × cax11 server (nbg1)" in result.output
    assert "2 × cx23 server (nbg1)" in result.output
    assert "3 × primary IPv4" in result.output
    assert "lb11" not in result.output
    assert "16.00" in result.output and "19.04" in result.output
    assert ("Provision these resources" in result.output) is (not non_interactive)


@pytest.mark.parametrize("non_interactive", [False, True])
def test_cap_compares_unrounded_decimal_total(tmp_path, provider, non_interactive):
    # GIVEN a gross total of 11.305 that displays as 11.31.
    ctx = project(
        tmp_path, non_interactive=non_interactive, max_monthly_cost=Decimal("11.3049")
    )
    provider["body"]["pricing"]["load_balancer_types"][0]["prices"][0]["price_monthly"][
        "gross"
    ] = "6.545"
    # WHEN the cap is just below the exact sum (in either consent mode).
    result = run_step(ctx, "y\n")
    # THEN no confirmation can override the cap.
    assert result.exit_code == 1
    assert "exceeds --max-monthly-cost" in result.output
    assert "Provision these resources" not in result.output


@pytest.mark.parametrize(
    "status,answers,non_interactive,cap,exit_code",
    [
        (401, "\n", False, None, 1),
        (503, "y\n", False, None, 0),
        (401, "", True, None, 0),
        (503, "", True, Decimal("100"), 1),
        (401, "y\n", False, Decimal("100"), 1),
    ],
)
def test_api_failure_requires_consent_or_provable_cap(
    tmp_path, provider, status, answers, non_interactive, cap, exit_code
):
    # GIVEN an unavailable pricing API and a real project.
    provider["status"] = status
    ctx = project(tmp_path, non_interactive=non_interactive, max_monthly_cost=cap)
    # WHEN the real step attempts an estimate.
    result = run_step(ctx, answers)
    # THEN resources remain visible and the configured consent/cap governs continuation.
    assert result.exit_code == exit_code, result.output
    assert "price could not be determined" in result.output
    assert "1 × cx23 server" in result.output
    assert "unavailable" in result.output
    assert "synthetic-token" not in result.output
    if cap is not None:
        assert "Cannot verify --max-monthly-cost" in result.output


@pytest.mark.parametrize(
    "category", ["server_types", "primary_ips", "load_balancer_types"]
)
def test_missing_prices_never_produce_partial_total(tmp_path, provider, category):
    # GIVEN a response missing a requested resource type/location.
    provider["body"]["pricing"][category] = []
    ctx = project(tmp_path, non_interactive=True, max_monthly_cost=Decimal("100"))
    # WHEN the step tries to check the cap.
    result = run_step(ctx)
    # THEN it identifies the missing price and aborts without claiming a partial total.
    assert result.exit_code == 1
    assert "Monthly price unavailable" in result.output
    assert "Cannot verify --max-monthly-cost" in result.output
    total = next(
        line for line in result.output.splitlines() if "Total per month" in line
    )
    assert total.count("unavailable") == 2


def test_wizard_routes_cost_before_auth_and_finalization(tmp_path, provider):
    # GIVEN contexts for each supported bootstrap provisioning mode.
    ctx = project(tmp_path, auth0_tenant="example.eu.auth0.com")
    # WHEN the real wizard chooses its steps.
    steps = steps_for(ctx)
    result = run_step(ctx, "y\n")
    # THEN preview precedes Auth0/finalize; only provisioning Hetzner flows include it.
    assert result.exit_code == 0, result.output
    assert "Total per month" in result.output
    assert (
        steps.index(ProjectStep)
        < steps.index(CostStep)
        < steps.index(Auth0Step)
        < steps.index(FinalizeStep)
    )
    ctx.shared_cluster = True
    assert CostStep in steps_for(ctx)
    ctx.cluster_config = {"owner": "sample-owner"}
    assert CostStep not in steps_for(ctx)
    ctx.cluster_config = None
    ctx.provider = "byos"
    assert CostStep not in steps_for(ctx)
    ctx.kind = "pitch"
    assert CostStep not in steps_for(ctx)


@pytest.mark.skipif(
    __import__("sys").platform != "darwin", reason="bootstrap requires macOS"
)
@pytest.mark.parametrize(
    "options,answers,expected",
    [
        (
            [],
            "n\ny\n\n",
            "Cancelled before provisioning; nothing was created in Hetzner.",
        ),
        (["--yes", "--max-monthly-cost", "1"], "", "exceeds --max-monthly-cost"),
    ],
)
def test_bootstrap_process_never_dispatches_after_cost_abort(
    tmp_path, provider, options, answers, expected
):
    import os
    import subprocess
    import sys
    from pathlib import Path

    from tests.vault_support import RealVault

    # GIVEN a configured, resumable real Git project with a real encrypted Vault,
    # plus local stand-ins for Hetzner, GitHub and the OS password provider.
    ctx = project(tmp_path)
    variables = ctx.deployment_dir / "group_vars/all.yml"
    encrypted = RealVault("synthetic-password").encrypt("synthetic-secret").decode()
    variables.write_text(
        "create_load_balancer: true\nsecret: !vault |\n"
        + "".join("  " + line + "\n" for line in encrypted.splitlines())
    )
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "Test",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Test",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    subprocess.run(
        ["git", "init", "-b", "main", str(ctx.project_dir)],
        check=True,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "-C", str(ctx.project_dir), "add", "."],
        check=True,
        capture_output=True,
        env=env,
    )
    subprocess.run(
        ["git", "-C", str(ctx.project_dir), "commit", "-m", "configured project"],
        check=True,
        capture_output=True,
        env=env,
    )
    shared = tmp_path / "shared.git"
    subprocess.run(
        ["git", "init", "--bare", str(shared)], check=True, capture_output=True
    )
    commands = tmp_path / "commands"
    commands.mkdir()
    log = tmp_path / "gh-log"
    gh = commands / "gh"
    gh.write_text(f"""#!{sys.executable}
import os, subprocess, sys
from pathlib import Path
args = sys.argv[1:]
with Path(os.environ['GH_LOG']).open('a') as log:
    log.write(' '.join(args) + '\\n')
if args[:2] == ['auth', 'status']:
    print("Token scopes: 'repo', 'workflow', 'read:packages'")
    sys.exit(0)
if args[:2] == ['repo', 'view'] and args[2].endswith('/deploy-your-startup'):
    sys.exit(subprocess.run(['git', '--git-dir', os.environ['SHARED_REPO'], 'rev-parse', '--git-dir'], capture_output=True).returncode)
sys.exit(1)
""")
    gh.chmod(0o755)
    security = commands / "security"
    security.write_text("#!/bin/sh\nprintf '%s' synthetic-password\n")
    security.chmod(0o755)
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    (home / ".ssh/id_ed25519.pub").write_text("ssh-ed25519 synthetic-public-key")
    (tmp_path / "token.env").write_text(
        "# Project: sample\nHETZNER_API_TOKEN=synthetic-token\n"
    )
    env.update(
        {
            "HOME": str(home),
            "PATH": str(commands) + os.pathsep + env["PATH"],
            "GH_LOG": str(log),
            "SHARED_REPO": str(shared),
            "HETZNER_BOOTSTRAP_TOKEN_FILE": str(tmp_path / "token.env"),
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
            "NO_COLOR": "1",
        }
    )
    # WHEN the actual bootstrap subprocess resumes and the cost gate aborts.
    result = subprocess.run(
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
            "--without-auth",
            "--project-name",
            ctx.project_name,
            "--base-domain",
            "example.com",
            "--github-username",
            ctx.github_username,
            "--output-dir",
            str(tmp_path),
            "--template",
            str(ctx.project_dir),
            "--no-verify",
            *options,
        ],
        input=answers,
        capture_output=True,
        text=True,
        env=env,
        timeout=45,
        check=False,
    )
    # THEN the estimate is printed, no push/workflow/secret write occurs, and
    # the original configured project survives for the next resume.
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert expected in output
    assert "Total per month" in output and "11.90" in output
    assert "/v1/pricing" in [path for path, _ in provider["requests"]]
    assert "workflow run" not in log.read_text()
    assert "secret set" not in log.read_text()
    assert "repo create" not in log.read_text()
    assert (
        subprocess.run(
            ["git", "-C", str(ctx.project_dir), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        == ""
    )


@pytest.mark.parametrize(
    "failure", ["connection", "malformed", "missing-location", "invalid-price"]
)
def test_unusable_provider_prices_remain_safe(tmp_path, provider, monkeypatch, failure):
    # GIVEN a local provider that is unreachable or returns unusable pricing.
    ctx = project(tmp_path, non_interactive=True, max_monthly_cost=Decimal("100"))
    if failure == "connection":
        monkeypatch.setenv("HCLOUD_API_URL", "http://127.0.0.1:1/v1")
    elif failure == "malformed":
        provider["body"] = {"pricing": []}
    elif failure == "missing-location":
        provider["body"]["pricing"]["server_types"][0]["prices"] = [
            price("3.00", "3.57", "hel1")
        ]
    else:
        provider["body"]["pricing"]["server_types"][0]["prices"][0]["price_monthly"][
            "gross"
        ] = "NaN"
    # WHEN the real step retrieves pricing and enforces the gross cap.
    result = run_step(ctx)
    # THEN it displays the plan, withholds a total and fails without a traceback.
    assert result.exit_code == 1, result.output
    assert "1 × cx23 server (fsn1)" in result.output
    assert "Cannot verify --max-monthly-cost" in result.output
    assert "Traceback" not in result.output


def test_default_template_without_load_balancer_and_equal_cap(tmp_path, provider):
    # GIVEN the actual template's disabled load balancer and an equal gross cap.
    ctx = project(
        tmp_path,
        "create_load_balancer: false\n",
        non_interactive=True,
        max_monthly_cost=Decimal("4.76"),
    )
    # WHEN the real step computes the server and primary IPv4 prices.
    result = run_step(ctx)
    # THEN equal costs pass and no load balancer charge is invented.
    assert result.exit_code == 0, result.output
    assert "lb11" not in result.output
    assert "4.00" in result.output and "4.76" in result.output


@pytest.mark.parametrize("amount", ["-1", "NaN", "Infinity", "not-a-number"])
def test_invalid_caps_fail_at_real_cli_boundary(tmp_path, amount):
    import os
    import subprocess
    import sys
    from pathlib import Path

    # GIVEN an invalid cap and no configured accounts.
    # WHEN the actual bootstrap process parses the requested option.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "cli.startup",
            "bootstrap",
            "--yes",
            "--max-monthly-cost",
            amount,
        ],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
        },
        timeout=30,
    )
    # THEN it rejects the amount before opening accounts or creating any project.
    assert result.returncode == 2
    assert "--max-monthly-cost" in result.stderr
    assert "non-negative decimal" in result.stderr
