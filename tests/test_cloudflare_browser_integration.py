"""Real browser checks; Cloudflare is replaced by a local HTTP dashboard."""

import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from cli import cloudflare


@pytest.fixture
def dashboard():
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            if self.path.endswith("/ready"):
                body = "<h1>User API Tokens</h1>"
            elif self.path.endswith("/delayed"):
                body = """<p>Checking session...</p><script>
                    setTimeout(() => {
                        document.body.innerHTML = '<h1>User API Tokens</h1>';
                    }, 3500);
                </script>"""
            else:
                body = '<h1>Sign in</h1><input name="email">'
            self.wfile.write(body.encode())

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("state", ["ready", "delayed", "denied"])
def test_login_waits_for_dashboard_with_stealth(
    monkeypatch, tmp_path, dashboard, state
):
    # GIVEN a fresh persistent Chrome profile and a local provider boundary.
    root, requests = dashboard
    target = f"{root}/profile/api-tokens/{state}"
    monkeypatch.setattr(cloudflare, "CHROME_USER_DATA_DIR", tmp_path / "profile")
    monkeypatch.setattr(cloudflare, "CF_TOKEN_URL", target)
    monkeypatch.setattr(cloudflare, "LOGIN_WAIT_TIMEOUT", 6000)

    async def scenario():
        async with cloudflare.CloudflareAutomation(headless=True) as bot:
            # WHEN the real login flow loads a ready, delayed or denied session.
            result = await bot.login(register=False)
            # THEN URL alone never passes and delayed rendering needs no reload.
            assert result is (state != "denied")
            assert await bot.page.get_by_role(
                "heading", name="User API Tokens"
            ).is_visible() is (state != "denied")
            assert requests.count(f"/profile/api-tokens/{state}") == 1
            # Init scripts run in the first page, new tabs and child frames.
            for page in [bot.page, await bot.page.context.new_page()]:
                await page.goto(root + "/ready")
                await page.evaluate("""() => {
                    const frame = document.createElement('iframe');
                    frame.src = '/ready'; document.body.append(frame);
                }""")
                await page.frame_locator("iframe").get_by_role("heading").wait_for()
                for frame in page.frames:
                    fingerprint = await frame.evaluate("""() => ({
                        webdriver: navigator.webdriver,
                        languages: navigator.languages,
                        platform: navigator.platform
                    })""")
                    assert fingerprint["webdriver"] is False
                    assert fingerprint["languages"] == ["en-US", "en"]
                    assert fingerprint["platform"] != "Win32"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "direct_form,success_shape", [(False, "box"), (True, "box"), (False, "curl")]
)
def test_create_token_with_current_cloudflare_form(
    monkeypatch, tmp_path, capsys, direct_form, success_shape
):
    # GIVEN Cloudflare's observed combobox labels and no legacy test IDs.
    import json
    from pathlib import Path

    submitted = []
    synthetic_token = "cfut_synthetic_local_browser_token_12345678"
    fixture = Path(__file__).parent / "fixtures" / "cloudflare-token-form.html"

    class Provider(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(fixture.read_bytes())

        def do_POST(self):
            submitted.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(synthetic_token.encode())

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(cloudflare, "CHROME_USER_DATA_DIR", tmp_path / "profile")
    monkeypatch.setattr(
        cloudflare,
        "CF_TOKEN_URL",
        f"http://127.0.0.1:{server.server_port}/profile/api-tokens"
        + f"?success={success_shape}"
        + ("&direct" if direct_form else ""),
    )
    monkeypatch.setattr(cloudflare, "FORM_READY_TIMEOUT", 1500)
    try:
        # WHEN the public entrypoint drives a real Chrome session end to end.
        token = cloudflare.create_api_token(
            token_name="example-production", headless=True
        )
        # THEN the server receives exactly the intended fields once, and the
        # one-time token is extracted without being printed.
        assert token == synthetic_token
        assert submitted == [
            {
                "name": "example-production",
                "permissions": [
                    {"resource": resource, "permission": permission, "level": level}
                    for resource, permission, level in cloudflare.DEFAULT_PERMISSIONS
                ],
            }
        ]
        assert synthetic_token not in capsys.readouterr().out
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
