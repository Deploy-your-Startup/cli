"""Exercise wizard selection and exact issuer matching over HTTPS."""

import json
import ssl
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import click
import pytest
from click.testing import CliRunner

from cli.wizard.authentication import choose_authentication
from cli.wizard.context import BootstrapContext
from cli.wizard.runner import steps_for
from cli.wizard.steps.finalize import FinalizeStep
from cli.wizard.steps.oidc import OidcStep


@pytest.fixture
def oidc_origin(tmp_path, monkeypatch):
    key = tmp_path / "key.pem"
    cert = tmp_path / "cert.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=localhost",
            "-addext",
            "subjectAltName=DNS:localhost",
        ],
        check=True,
        capture_output=True,
    )
    monkeypatch.setenv("SSL_CERT_FILE", str(cert))

    class Provider(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != "/realms/startup/.well-known/openid-configuration":
                self.send_error(404)
                return
            issuer = f"https://localhost:{server.server_port}/realms/startup"
            body = json.dumps(
                {
                    "issuer": issuer,
                    "authorization_endpoint": f"{issuer}/authorize",
                    "token_endpoint": f"{issuer}/token",
                    "jwks_uri": f"{issuer}/certs",
                }
            ).encode()
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    with ThreadingHTTPServer(("127.0.0.1", 0), Provider) as server:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
        worker = Thread(target=server.serve_forever)
        worker.start()
        try:
            yield f"https://localhost:{server.server_port}/realms/startup"
        finally:
            server.shutdown()
            worker.join()


@pytest.fixture
def wizard(tmp_path):
    template = tmp_path / "template"
    template.mkdir()
    (template / "copier.yml").write_text("project_name: example\n")
    (template / "startup-template.yml").write_text("schema: 1\nauthentication: oidc\n")
    return BootstrapContext(
        "demo",
        "demo.example",
        "",
        "owner",
        "18",
        "",
        tmp_path,
        template_source=str(template),
    )


def test_select_keycloak_real_realm_discovery(oidc_origin, wizard):
    # GIVEN an OIDC-capable template and an existing Keycloak-style realm.
    command = click.command()(lambda: choose_authentication(wizard))
    # WHEN the user selects Keycloak and enters the client details.
    result = CliRunner().invoke(
        command, input=f"3\n{oidc_origin}\nstartup-web\nlocal-example-secret\n"
    )
    # THEN the wizard validates discovery and schedules OIDC before the first push.
    assert result.exit_code == 0, result.output
    assert "Keycloak (existing realm)" in result.output
    assert "Other OIDC provider" in result.output and "Auth0" in result.output
    assert "https://demo.example/oauth2/callback" in result.output
    assert "local-example-secret" not in result.output
    assert wizard.oidc_issuer == oidc_origin and wizard.oidc_client_id == "startup-web"
    assert steps_for(wizard)[-2:] == [OidcStep, FinalizeStep]


def test_wrong_issuer_stops_before_reading_credentials(oidc_origin, wizard):
    # GIVEN the same discovery endpoint with a different configured issuer.
    wizard.oidc_issuer = oidc_origin + "/"
    wizard.oidc_client_id = "startup-web"
    wizard.oidc_client_secret_stdin = True
    wizard.non_interactive = True
    # WHEN authentication is prepared, THEN the mismatch prevents provisioning.
    command = click.command()(lambda: choose_authentication(wizard))
    result = CliRunner().invoke(command)
    assert result.exit_code == 1
    assert "does not exactly match" in result.output
    assert wizard.oidc_client_secret is None and not wizard.project_dir.exists()
