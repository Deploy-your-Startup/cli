"""Drive the actual CLI against local GitHub, DNS and HTTP boundaries."""

import json
import os
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from click.testing import CliRunner

from cli.startup import cli
from cli.vault.process import encrypt


@pytest.fixture
def project(tmp_path, monkeypatch):
    # GIVEN real project files, a GitHub CLI stand-in and a local HTTP server.
    (tmp_path / ".copier-answers.yml").write_text(
        "project_name: demo\nbase_domain: example.com\ngithub_username: example\n"
    )
    (tmp_path / ".github/workflows").mkdir(parents=True)
    (tmp_path / ".github/workflows/build-and-deploy-backend.yml").write_text(
        "name: Backend\n"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text("""#!/usr/bin/env python3
import json,os
if os.environ.get('VERIFY_GH_ERROR'):raise SystemExit(1)
print(json.dumps({'workflow_runs':[{'event':'push','status':'completed','conclusion':os.environ.get('VERIFY_CONCLUSION','success'),'html_url':'https://github.com/example/demo/actions/runs/42'}]}))
""")
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ["PATH"])

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            payload = {"status": os.environ.get("VERIFY_HEALTH", "ok")}
            if self.path == "/servers":
                payload = {
                    "servers": [
                        {
                            "name": "demo-master-0",
                            "public_net": {"ipv4": {"ip": "192.0.2.10"}},
                        }
                    ]
                }
            elif self.path == "/load_balancers":
                payload = {"load_balancers": []}
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original_get = httpx.get

    def get(url, **kwargs):
        if os.environ.get("VERIFY_TLS_ERROR"):
            raise httpx.ConnectError("synthetic certificate failure")
        return original_get(
            f"http://127.0.0.1:{server.server_port}/api/health", **kwargs
        )

    monkeypatch.setattr(httpx, "get", get)
    client_get = httpx.Client.get

    def cloud_get(client, url, **kwargs):
        return client_get(
            client,
            f"http://127.0.0.1:{server.server_port}/" + str(url).rsplit("/", 1)[-1],
            **kwargs,
        )

    monkeypatch.setattr(httpx.Client, "get", cloud_get)
    original_resolve = socket.getaddrinfo

    def resolve(host, *args, **kwargs):
        if host == "example.com":
            return [
                (
                    socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    (os.environ.get("VERIFY_DNS", "192.0.2.10"), 443),
                )
            ]
        return original_resolve(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    yield tmp_path
    server.shutdown()
    thread.join()


def launch(project):
    # WHEN the public command checks the project without modifying it.
    return CliRunner().invoke(
        cli,
        ["verify", "--working-directory", str(project), "--server-ip", "192.0.2.10"],
    )


def test_success_requires_actions_dns_and_application_health(project):
    before = (project / ".copier-answers.yml").read_bytes()
    result = launch(project)
    # THEN the user sees a verified live site and the files remain unchanged.
    assert result.exit_code == 0, result.output
    assert "Verified live:" in result.output
    assert "example.com A 192.0.2.10" in result.output
    assert (project / ".copier-answers.yml").read_bytes() == before


@pytest.mark.parametrize(
    "environment,value,message",
    [
        ("VERIFY_CONCLUSION", "failure", "Actions failed"),
        ("VERIFY_GH_ERROR", "1", "Cannot read GitHub Actions"),
        ("VERIFY_DNS", "192.0.2.99", "DNS mismatch"),
        ("VERIFY_TLS_ERROR", "1", "HTTPS health check failed"),
        ("VERIFY_HEALTH", "unhealthy", "HTTPS health check did not return"),
    ],
)
def test_incomplete_deployment_is_never_reported_live(
    project, monkeypatch, environment, value, message
):
    # GIVEN an unsuccessful external check, WHEN verifying, THEN return failure.
    monkeypatch.setenv(environment, value)
    result = launch(project)
    assert result.exit_code == 1, result.output
    assert message in result.output
    assert "Verified live:" not in result.output


def test_missing_project_is_actionable(tmp_path):
    # GIVEN no generated project, WHEN verifying, THEN explain what to select.
    result = CliRunner().invoke(cli, ["verify", "--working-directory", str(tmp_path)])
    assert result.exit_code == 1
    assert ".copier-answers.yml" in result.output


def test_server_ip_is_read_with_real_vault_without_exposing_token(project, monkeypatch):
    # GIVEN a real encrypted project token and local Hetzner API stand-in,
    # WHEN verifying without --server-ip, THEN decrypt via the real CLI,
    # discover the server and never print its credential.
    password = "synthetic-verification-password"
    token = "synthetic-project-token"
    deployment = project / "deployment"
    deployment.mkdir()
    (deployment / "hcloud_token_production").write_bytes(
        encrypt(token.encode(), password)
    )
    monkeypatch.setenv("STARTUP_VAULT_PASSWORD", password)
    result = CliRunner().invoke(cli, ["verify", "--working-directory", str(project)])
    assert result.exit_code == 0, result.output
    assert "example.com A 192.0.2.10" in result.output
    assert token not in result.output and password not in result.output


def test_bootstrap_exposes_explicit_verification_controls():
    # GIVEN the real CLI, WHEN reading help, THEN unattended waits are controllable.
    result = CliRunner().invoke(cli, ["bootstrap", "--help"])
    assert result.exit_code == 0
    assert "--verify / --no-verify" in result.output
    assert "--verify-timeout" in result.output
