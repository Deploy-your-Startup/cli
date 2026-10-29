"""Validate and supervise a foreground cloudflared Quick Tunnel."""

import ipaddress
import os
import re
import selectors
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import click

PUBLIC_URL = re.compile(r"https://[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com\b")


def validate_origin(url: str) -> tuple[str, int]:
    """Reject nonlocal services and secrets before a subprocess can log them."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        port = (
            parsed.port
            if parsed.port is not None
            else (443 if parsed.scheme == "https" else 80)
        )
        local = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        local = False
    if (
        not local
        or not 1 <= port <= 65535
        or parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or any(char.isspace() or ord(char) < 32 for char in url)
    ):
        raise click.BadParameter(
            "Use an HTTP(S) loopback URL (localhost, 127.0.0.1 or [::1]) "
            "without credentials, query parameters or fragments.",
            param_hint="--url",
        )
    return host, port


def check_origin(host: str, port: int) -> None:
    try:
        with socket.create_connection((host, port), timeout=3):
            pass
    except OSError as exc:
        raise click.ClickException(
            "Cannot connect to the local dev server. Start it first and check --url."
        ) from exc


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


@contextmanager
def handle_termination():
    def interrupt(_signum, _frame):
        raise KeyboardInterrupt

    previous = signal.signal(signal.SIGTERM, interrupt)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)


def watch_tunnel(process: subprocess.Popen, startup_timeout: int) -> None:
    """Read bounded chunks so partial log lines cannot defeat the deadline."""
    deadline = time.monotonic() + startup_timeout
    recent = ""
    public_url = None
    connected = False
    announced = False
    assert process.stdout is not None
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            if not announced and time.monotonic() >= deadline:
                raise click.ClickException(
                    "Timed out waiting for cloudflared to connect. Check network access "
                    "and the logs, or increase --startup-timeout."
                )
            for key, _ in selector.select(timeout=0.1):
                chunk = os.read(key.fd, 8192)
                if not chunk:
                    raise click.ClickException(
                        "cloudflared stopped unexpectedly. Check its logs above."
                    )
                text = chunk.decode("utf-8", errors="replace")
                click.echo(text, nl=False, err=True)
                recent = (recent + text)[-16384:]
                match = PUBLIC_URL.search(recent)
                if match:
                    public_url = match.group()
                connected = connected or "Registered tunnel connection" in recent
            if public_url and connected and not announced:
                click.echo(f"HTTPS URL: {public_url}")
                click.echo(
                    "Tunnel connected. Ctrl+C to stop; the URL changes next run."
                )
                announced = True


def run_tunnel(url: str, startup_timeout: int) -> None:
    host, port = validate_origin(url)
    executable = shutil.which("cloudflared")
    if executable is None:
        raise click.ClickException(
            "cloudflared is missing. On macOS, run `brew install cloudflared`, "
            "or see https://developers.cloudflare.com/tunnel/downloads/ then retry."
        )
    check_origin(host, port)
    click.echo(
        "! Public access: anyone with the URL can reach your local service. "
        "Use test data and application authentication; HTTPS is not access control.",
        err=True,
    )
    try:
        with (
            handle_termination(),
            tempfile.TemporaryDirectory(prefix="startup-tunnel-") as temporary,
        ):
            config = Path(temporary) / "config.yml"
            config.write_text("{}\n")
            supervise_tunnel(executable, config, url, startup_timeout)
    except KeyboardInterrupt:
        click.echo("Tunnel stopped.", err=True)
        raise click.exceptions.Exit(130) from None


def supervise_tunnel(executable: str, config: Path, url: str, timeout: int) -> None:
    command = [
        executable,
        "tunnel",
        "--config",
        str(config),
        "--no-autoupdate",
        "--url",
        url,
        "--loglevel",
        "info",
    ]
    try:
        process = subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env={
                key: value
                for key, value in os.environ.items()
                if not key.startswith("TUNNEL_")
            },
        )
    except OSError as exc:
        raise click.ClickException(
            "Could not launch cloudflared. Check its installation."
        ) from exc
    try:
        watch_tunnel(process, timeout)
    finally:
        stop_process(process)
        if process.stdout is not None:
            process.stdout.close()
