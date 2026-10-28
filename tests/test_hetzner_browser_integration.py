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
    pending["announcement"] = announcement
    monkeypatch.setattr(config, "CHROMIUM_USER_DATA_DIR", tmp_path / "profile")
    monkeypatch.setattr(config, "HETZNER_BASE_URL", root)
    monkeypatch.setattr(config, "HETZNER_PROJECTS_URL", root + "/projects")

    async def scenario():
        async with HetznerAutomation(headless=True) as bot:
            # WHEN the public step creates a project in a real Chromium session.
            result = await bot.create_project("sample-startup")
            # THEN it is created once and the browser ends up inside it.
            assert result is True
            assert created == ["sample-startup"]
            assert bot.page.url == f"{root}/projects/1002"

    asyncio.run(scenario())


@pytest.mark.parametrize("engine", ["chromium", "chrome"])
def test_hetzner_sessions_survive_restart_and_are_shared_with_konsoleh(
    monkeypatch, tmp_path, console, engine
):
    # GIVEN the shared Chromium profile and a persistent provider session cookie.
    from cli.browser_setup import browser_check
    from cli.hetzner.robot import HetznerKonsoleHAutomation

    monkeypatch.setenv("STARTUP_BROWSER", engine)
    if engine == "chrome" and not browser_check()[0]:
        pytest.skip(
            "System Chrome is required for the optional Chrome session scenario"
        )
    root, _, _ = console
    legacy = tmp_path / "chrome-profile"
    legacy.mkdir()
    marker = legacy / "preserved.txt"
    marker.write_text("existing extension/profile state")
    monkeypatch.setattr(config, "CHROMIUM_USER_DATA_DIR", tmp_path / "chromium-profile")

    async def scenario():
        async with HetznerAutomation(headless=True) as bot:
            await bot.page.goto(root + "/projects")
            await bot.page.context.add_cookies(
                [
                    {
                        "name": "session",
                        "value": "local-fixture",
                        "url": root,
                        "expires": 4102444800,
                    }
                ]
            )
        # WHEN restarting both public browser entrypoints on the same profile.
        for browser in (HetznerKonsoleHAutomation, HetznerAutomation):
            async with browser(headless=True) as bot:
                await bot.page.goto(root + "/projects")
                # THEN the persistent cookie remains available after each restart.
                if engine == "chrome":
                    session = await bot.page.context.new_cdp_session(bot.page)
                    arguments = (await session.send("Browser.getBrowserCommandLine"))[
                        "arguments"
                    ]
                    assert "--disable-extensions" not in arguments
                    assert (
                        "--disable-component-extensions-with-background-pages"
                        not in arguments
                    )
                cookies = await bot.page.context.cookies(root)
                assert any(
                    c["name"] == "session" and c["value"] == "local-fixture"
                    for c in cookies
                )

    asyncio.run(scenario())
    assert marker.read_text() == "existing extension/profile state"
    assert (
        tmp_path
        / ("chrome-profile" if engine == "chrome" else "chromium-profile")
        / "Default"
    ).is_dir()
