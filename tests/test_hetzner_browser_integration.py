"""Real browser checks; the Hetzner console is replaced by a local HTTP server."""

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import TypedDict

import pytest
from click import ClickException

from cli.hetzner import _output as output
from cli.hetzner import config
from cli.hetzner.automation import HetznerAutomation

FIXTURE = Path(__file__).parent / "fixtures" / "hetzner-projects.html"
TEST_TOKEN = "aB1d" * 16
TOKEN_PAGE = """
<div data-test="tokens-table">Beschreibung Berechtigungen</div>
<script>
setTimeout(() => {
    const button = document.createElement('button');
    button.dataset.test = 'tokens-add-btn';
    button.textContent = 'API-Token hinzufügen';
    button.onclick = () => document.querySelector('#token-dialog').hidden = false;
    document.body.append(button);
}, 10500);
</script>
<form id="token-dialog" hidden>
    <input name="description" required>
    <button data-test="confirm-dialog-accept">API-Token hinzufügen</button>
</form>
<code class="token-value" hidden></code>
<script>
const form = document.querySelector('#token-dialog');
form.onsubmit = async (event) => {
    event.preventDefault();
    const response = await fetch('/api/token', {
        method: 'POST',
        body: JSON.stringify({name: form.elements.description.value}),
    });
    const data = await response.json();
    const result = document.querySelector('code');
    result.textContent = data.token;
    result.hidden = false;
    form.hidden = true;
};
</script>
"""


class ConsoleState(TypedDict):
    announcement: bool
    tokens: list[str]


@pytest.fixture
def console():
    projects = [{"id": 1001, "name": "existing"}]
    pending: ConsoleState = {"announcement": False, "tokens": []}
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
            elif self.path == "/projects/1001":
                self._reply(
                    "text/html",
                    b'<a href="/projects/1001/security">Sicherheit</a>',
                )
            elif self.path == "/projects/1001/security":
                self._reply(
                    "text/html",
                    b'<a href="/projects/1001/security/tokens">API-Tokens</a>',
                )
            elif self.path == "/projects/1001/security/tokens":
                self._reply("text/html", TOKEN_PAGE.encode())
            else:
                self._reply("text/html", f"<h1>Project {self.path}</h1>".encode())

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/api/token":
                pending["tokens"].append(payload["name"])
                self._reply(
                    "application/json", json.dumps({"token": TEST_TOKEN}).encode()
                )
                return
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


@pytest.mark.parametrize("wait_for_button", [True, False])
def test_create_token_waits_for_delayed_add_button(
    monkeypatch, tmp_path, console, wait_for_button
):
    # GIVEN the token table is available before the add button renders.
    root, pending, _ = console
    monkeypatch.setenv("STARTUP_BROWSER", "chromium")
    monkeypatch.setattr(config, "CHROME_USER_DATA_DIR", tmp_path / "profile")
    monkeypatch.setattr(config, "HETZNER_BASE_URL", root)
    if not wait_for_button:
        monkeypatch.setattr(config, "NAVIGATION_TIMEOUT", 250)

    def fail_on_prompt(*args, **kwargs):
        pytest.fail("The ready token button should not require manual input")

    monkeypatch.setattr(output, "ask", fail_on_prompt)

    async def scenario():
        async with HetznerAutomation(headless=True) as bot:
            await bot.page.goto(f"{root}/projects/1001")
            # WHEN the browser runs the standard token creation flow.
            if wait_for_button:
                # THEN the late control works and only one token is submitted.
                assert await bot.create_api_token("deploy-cli") == TEST_TOKEN
                assert pending["tokens"] == ["deploy-cli"]
            else:
                # THEN it fails before attempting to submit or requesting a token.
                with pytest.raises(ClickException, match="API token creation button"):
                    await bot.create_api_token("deploy-cli")
                assert pending["tokens"] == []

    asyncio.run(scenario())
