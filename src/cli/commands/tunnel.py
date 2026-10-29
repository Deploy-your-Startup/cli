"""Account-free HTTPS access to a local development server."""

import click

from cli.dev_tunnel import run_tunnel


@click.command()
@click.option(
    "--url", required=True, help="Local HTTP(S) origin, e.g. http://localhost:8000."
)
@click.option(
    "--startup-timeout",
    type=click.IntRange(min=1),
    default=60,
    show_default=True,
    help="Seconds to wait for a public URL and a connected tunnel.",
)
def tunnel(url: str, startup_timeout: int) -> None:
    """Expose a local dev server through a temporary public HTTPS URL.

    Requires cloudflared on PATH; no Cloudflare account or domain needed.
    Anyone with the URL can reach your service. Keep application authentication
    enabled and use test data only. Ctrl+C stops the tunnel. The hostname changes
    each run; Quick Tunnels do not support Server-Sent Events (SSE).
    """
    run_tunnel(url, startup_timeout)
