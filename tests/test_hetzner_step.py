"""GIVEN saved credentials, WHEN bootstrap connects, THEN project scope is respected."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import httpx
import pytest

from cli.hetzner import config
from cli.hetzner.credentials import save_token
from cli.wizard.context import BootstrapContext
from cli.wizard.steps.hetzner import HetznerStep


@pytest.mark.parametrize(
    "saved_project,explicit,expected",
    [
        ("other-startup", None, False),
        ("my-startup", "explicit-token", False),
        ("my-startup", None, True),
    ],
)
def test_saved_token_is_used_only_for_the_requested_project(
    tmp_path, monkeypatch, saved_project, explicit, expected
):
    # GIVEN real credential files and a local stand-in for the Hetzner API.
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"servers": []}')

        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    real_get = httpx.get
    # Replace only the provider boundary; our credential and wizard code stays real.
    monkeypatch.setattr(
        httpx,
        "get",
        lambda _url, **kwargs: real_get(
            f"http://127.0.0.1:{server.server_port}", **kwargs
        ),
    )
    monkeypatch.setattr(config, "TOKEN_FILE", tmp_path / "credentials/hetzner.env")
    save_token("saved-synthetic-token", saved_project)
    ctx = BootstrapContext(
        project_name="my-startup",
        base_domain="example.com",
        additional_domains="",
        github_username="sample",
        postgres_version="18.6",
        sentry_dsn="",
        output_dir=Path(tmp_path),
        hetzner_token=explicit,
    )
    try:
        # WHEN the actual bootstrap step evaluates whether saved setup can be reused.
        assert HetznerStep().check(ctx) is expected
        # THEN an explicit token takes priority and another project's token is untouched.
        assert requests == (["Bearer saved-synthetic-token"] if expected else [])
        assert ctx.hetzner_token == ("saved-synthetic-token" if expected else explicit)
    finally:
        server.shutdown()
        thread.join()
