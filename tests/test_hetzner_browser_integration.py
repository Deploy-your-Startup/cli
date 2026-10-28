"""Real browser checks; the Hetzner console is replaced by a local HTTP server."""

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from cli.hetzner import config
from cli.hetzner.automation import HetznerAutomation

FIXTURE = Path(__file__).parent / "fixtures" / "hetzner-projects.html"


@pytest.fixture
def console():
    projects = [{"id": 1001, "name": "existing"}]
    pending = {"announcement": False}
    created = []

    class Provider(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/projects":
                self._reply("text/html", FIXTURE.read_bytes())
            elif self.path == "/api/state":
                # The changelog opens once, like after a fresh login or release.
                body = {"projects": projects, "announcement": pending["announcement"]}
                pending["announcement"] = False
                self._reply("application/json", json.dumps(body).encode())
            else:
                self._reply("text/html", f"<h1>Project {self.path}</h1>".encode())

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            created.append(payload["name"])
            projects.append({"id": 1001 + len(created), **payload})
            self._reply("application/json", b"{}")

        def _reply(self, content_type, body):
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", pending, created
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("announcement", [True, False])
def test_create_project_clicks_through_console_announcement(
    monkeypatch, tmp_path, console, announcement
):
    # GIVEN the observed projects markup, optionally covered by the changelog.
    root, pending, created = console
    monkeypatch.setenv("STARTUP_BROWSER", "chromium")
    pending["announcement"] = announcement
    monkeypatch.setattr(config, "CHROME_USER_DATA_DIR", tmp_path / "profile")
    monkeypatch.setattr(config, "HETZNER_BASE_URL", root)
    monkeypatch.setattr(config, "HETZNER_PROJECTS_URL", root + "/projects")

    async def scenario():
        async with HetznerAutomation(headless=True) as bot:
            # WHEN the public step creates a project in a real Chrome session.
            result = await bot.create_project("sample-startup")
            # THEN it is created once and the browser ends up inside it.
            assert result is True
            assert created == ["sample-startup"]
            assert bot.page.url == f"{root}/projects/1002"

    asyncio.run(scenario())
