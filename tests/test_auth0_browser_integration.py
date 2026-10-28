"""Auth0 verification uses real managed Chromium against a local TLS boundary."""

import ssl
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from typing import TypedDict
from urllib.parse import urlencode, urlsplit

import pytest
from playwright.sync_api import Browser, BrowserType

from cli.auth0_commands import validate_live_login


class LoginState(TypedDict):
    initial_sessions: list[str]
    logins: int


@pytest.fixture
def login_provider(tmp_path):
    # The certificate is disposable test data, never a provider credential.
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-days",
            "1",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-subj",
            "/CN=localhost",
        ],
        capture_output=True,
        check=True,
    )
    state: LoginState = {"initial_sessions": [], "logins": 0}

    class Provider(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path)
            cookie = self.headers.get("Cookie", "")
            if path.path == "/private_api/session":
                if state["logins"] == 0 or "_oauth2_proxy=" not in cookie:
                    state["initial_sessions"].append(cookie)
                self.send_response(200 if "_oauth2_proxy=" in cookie else 401)
            elif path.path == "/oauth2/start":
                self.send_response(302)
                if path.query:
                    state["logins"] += 1
                    self.send_header(
                        "Set-Cookie",
                        "_oauth2_proxy=local-fixture; Secure; HttpOnly; Path=/",
                    )
                    self.send_header("Location", "/")
                else:
                    self.send_header(
                        "Location",
                        "https://tenant.example/authorize?"
                        + urlencode({"redirect_uri": origin + "/oauth2/callback"}),
                    )
            else:
                self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Local authentication fixture")

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(cert, key)
    server.socket = tls.wrap_socket(server.socket, server_side=True)
    origin = f"https://localhost:{server.server_port}"
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield origin, state
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_auth0_managed_browser_does_not_reuse_login_state(monkeypatch, login_provider):
    # GIVEN a local Auth0/protected API boundary and a disposable TLS certificate.
    origin, state = login_provider
    launch = BrowserType.launch
    new_context = Browser.new_context
    launches = []

    def headless_launch(browser_type, *args, **kwargs):
        # Only display availability differs from the production launch.
        launches.append(kwargs.copy())
        return launch(browser_type, *args, **{**kwargs, "headless": True})

    def local_tls_context(browser, *args, **kwargs):
        return new_context(browser, *args, **{**kwargs, "ignore_https_errors": True})

    monkeypatch.setattr(BrowserType, "launch", headless_launch)
    monkeypatch.setattr(Browser, "new_context", local_tls_context)
    # WHEN verifying twice through the public entrypoint with real Chromium.
    for _ in range(2):
        validate_live_login(origin, "tenant.example", timeout=10)
    # THEN production requests headed managed Chromium, and every run signs in anew.
    assert launches == [{"headless": False}, {"headless": False}]
    assert state["logins"] == 2
    assert state["initial_sessions"] == ["", "", "", ""]
