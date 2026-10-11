"""GIVEN real login flows and HTTP pages, WHEN sessions expire, THEN require login."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx
import pytest
from playwright.async_api import TimeoutError as BrowserTimeout

from cli.hetzner import config
from cli.hetzner.automation import HetznerAutomation


@pytest.mark.parametrize("expired", [True, False])
def test_login_uses_rendered_projects_not_url(monkeypatch, capsys, expired):
    # GIVEN a local provider showing either a login form or a visible project.
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            if expired and self.path == "/projects":
                self.wfile.write(b'<input name="email">')
            else:
                self.wfile.write(b'<a data-projectname="sample">sample</a>')

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = f"http://127.0.0.1:{server.server_port}"
    monkeypatch.setattr(config, "HETZNER_PROJECTS_URL", root + "/projects")
    monkeypatch.setattr(config, "HETZNER_LOGIN_URL", root + "/login")

    class BrowserPage:
        """Only the browser boundary is replaced, backed by real HTTP responses."""

        def __init__(self):
            self.content = ""
            self.url = ""

        async def goto(self, url, *, wait_until):
            self.url = url
            async with httpx.AsyncClient() as client:
                self.content = (await client.get(url)).text

        def locator(self, selector):
            assert selector == config.SELECTORS_PROJECTS_READY
            return self

        @property
        def first(self):
            return self

        async def wait_for(self, *, state, timeout):
            if 'data-projectname="sample"' not in self.content:
                raise BrowserTimeout("No visible project")

    bot = HetznerAutomation()
    monkeypatch.setattr(bot, "_page", BrowserPage())
    try:
        # WHEN the public login entrypoint inspects the actual provider pages.
        result = asyncio.run(bot.login())
        # THEN expired sessions require login even when the URL stays /projects.
        assert result is True
        assert requests == (["/projects", "/login"] if expired else ["/projects"])
        output = capsys.readouterr().out
        assert ("Please log in via the browser" in output) is expired
        assert ("Already logged in" in output) is not expired
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
