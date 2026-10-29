"""Real CLI lifecycle tests; only the external cloudflared boundary is replaced."""

import http.server
import json
import os
import selectors
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src"
PUBLIC_URL = "https://example-dev-tunnel.trycloudflare.com"
STAND_IN = r"""
import json, os, signal, sys, time, urllib.request
from pathlib import Path
root = Path(os.environ["STARTUP_TUNNEL_TEST_ROOT"])
args = sys.argv[1:]
config = Path(args[args.index("--config") + 1])
(root / "state.json").write_text(json.dumps({
    "pid": os.getpid(), "args": args, "config": str(config),
    "contents": config.read_text(), "token": os.environ.get("TUNNEL_TOKEN"),
}))
mode = os.environ.get("STARTUP_TUNNEL_TEST_MODE", "success")
if mode == "stubborn":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if mode == "fail":
    print("ERR network denied", flush=True)
    sys.exit(9)
if mode == "partial":
    print("unfinished log", end="", flush=True)
elif mode != "hang":
    with urllib.request.urlopen(args[args.index("--url") + 1]) as response:
        assert response.read() == b"development fixture"
    print("https://example-dev-tunnel.trycloudflare.com", flush=True)
    if mode != "url-only":
        print("INF Registered tunnel connection connIndex=0", flush=True)
if mode == "exit":
    time.sleep(0.3)
    sys.exit(0)
while True:
    time.sleep(0.1)
"""


@pytest.fixture
def origin():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"development fixture")

        def log_message(self, format, *args):
            pass

    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join()


@pytest.fixture
def tunnel_env(tmp_path):
    executable = tmp_path / "cloudflared"
    executable.write_text(f"#!{sys.executable}\n" + STAND_IN)
    executable.chmod(0o755)
    return {
        **os.environ,
        "PATH": str(tmp_path),
        "PYTHONPATH": str(SOURCE),
        "STARTUP_TUNNEL_TEST_ROOT": str(tmp_path),
        "TUNNEL_TOKEN": "must-not-be-inherited",
    }


def run_cli(env, *args):
    return subprocess.run(
        [sys.executable, "-m", "cli.startup", "tunnel", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )


def assert_cleaned_up(tmp_path):
    state = json.loads((tmp_path / "state.json").read_text())
    assert not Path(state["config"]).exists()
    with pytest.raises(ProcessLookupError):
        os.kill(state["pid"], 0)
    return state


def await_endpoint(process):
    """Use unbuffered reads to avoid readline hanging on an absent endpoint."""
    received = b""
    assert process.stdout is not None
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for key, _ in selector.select(timeout=0.1):
                chunk = os.read(key.fd, 8192)
                assert chunk, received.decode()
                received += chunk
                if f"HTTPS URL: {PUBLIC_URL}".encode() in received:
                    return received.decode()
    pytest.fail(f"No endpoint printed: {received!r}")


@pytest.mark.parametrize("stop_signal", [signal.SIGINT, signal.SIGTERM])
@pytest.mark.parametrize("mode", ["success", "stubborn"])
def test_connected_tunnel_exposes_endpoint_and_stops_child(
    tmp_path, tunnel_env, origin, stop_signal, mode
):
    # GIVEN a local HTTP server and cloudflared with an inherited account token.
    tunnel_env["STARTUP_TUNNEL_TEST_MODE"] = mode
    process = subprocess.Popen(
        [sys.executable, "-m", "cli.startup", "tunnel", "--url", origin],
        env=tunnel_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        # WHEN the real CLI connects and the user interrupts it.
        output = await_endpoint(process)
        process.send_signal(stop_signal)
        stdout, stderr = process.communicate(timeout=12)
        # THEN the URL is copyable, exposure is explicit, and the child is reaped.
        assert process.returncode == 130
        assert output.count("HTTPS URL:") + stdout.decode().count("HTTPS URL:") == 1
        assert "anyone with the URL" in stderr.decode()
        assert "Tunnel stopped" in stderr.decode()
        state = assert_cleaned_up(tmp_path)
        assert state["contents"] == "{}\n"
        assert state["token"] is None
        assert state["args"][-4:] == ["--url", origin, "--loglevel", "info"]
        assert "--no-autoupdate" in state["args"]
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()


@pytest.mark.parametrize("mode", ["hang", "partial", "url-only"])
def test_startup_timeout_reaps_child_without_announcing_endpoint(
    tmp_path, tunnel_env, origin, mode
):
    # GIVEN a provider that stalls, even after advertising a URL.
    tunnel_env["STARTUP_TUNNEL_TEST_MODE"] = mode
    # WHEN the startup deadline expires.
    result = run_cli(tunnel_env, "--url", origin, "--startup-timeout", "5")
    # THEN no connected endpoint is claimed and no process/config is left behind.
    assert result.returncode == 1, result.stderr
    assert "Timed out" in result.stderr
    assert "HTTPS URL:" not in result.stdout
    assert_cleaned_up(tmp_path)


@pytest.mark.parametrize("mode", ["fail", "exit"])
def test_provider_exit_is_failure_even_after_connecting(
    tmp_path, tunnel_env, origin, mode
):
    # GIVEN a provider that exits before or after connecting.
    tunnel_env["STARTUP_TUNNEL_TEST_MODE"] = mode
    # WHEN the real CLI supervises it.
    result = run_cli(tunnel_env, "--url", origin)
    # THEN even an unexpected zero exit does not imply a running tunnel.
    assert result.returncode == 1
    assert "stopped unexpectedly" in result.stderr
    if mode == "fail":
        assert "network denied" in result.stderr
        assert "HTTPS URL:" not in result.stdout
    assert_cleaned_up(tmp_path)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com:8000",
        "http://0.0.0.0:8000",
        "ftp://localhost:8000",
        "http://user:secret@localhost:8000",
        "http://localhost:8000/?token=secret",
        "http://localhost:8000/#secret",
        "http://localhost:bad",
        "not-a-url",
        "http://[broken",
        "http://localhost:0",
        "http://localhost:65536",
        "http://local\nhost:8000",
    ],
)
def test_invalid_or_sensitive_origin_is_rejected_before_launch(
    tmp_path, tunnel_env, url
):
    # GIVEN a nonlocal, malformed or sensitive target.
    # WHEN it is passed to the public entrypoint.
    result = run_cli(tunnel_env, "--url", url)
    # THEN no public exposure occurs and secrets are not echoed.
    assert result.returncode == 2, result.stderr
    assert "loopback URL" in result.stderr
    assert "secret" not in result.stdout + result.stderr
    assert not (tmp_path / "state.json").exists()


def test_missing_cloudflared_has_install_guidance(tmp_path, tunnel_env, origin):
    # GIVEN no cloudflared on PATH.
    (tmp_path / "cloudflared").unlink()
    # WHEN starting a tunnel.
    result = run_cli(tunnel_env, "--url", origin)
    # THEN the CLI explains how to install it, without installing anything.
    assert result.returncode == 1
    assert "brew install cloudflared" in result.stderr
    assert not (tmp_path / "state.json").exists()


def test_unreachable_local_service_is_not_exposed(tmp_path, tunnel_env):
    # GIVEN a reserved but non-listening local port.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        url = f"http://127.0.0.1:{reserved.getsockname()[1]}"
        # WHEN starting a tunnel.
        result = run_cli(tunnel_env, "--url", url)
    # THEN cloudflared never starts.
    assert result.returncode == 1
    assert "Start it first" in result.stderr
    assert not (tmp_path / "state.json").exists()


def test_help_explains_lifetime_and_limitations(tunnel_env):
    result = run_cli(tunnel_env, "--help")
    assert result.returncode == 0
    for text in ["--url", "--startup-timeout", "Anyone", "Ctrl+C", "SSE"]:
        assert text in result.stdout
