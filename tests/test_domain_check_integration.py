"""GIVEN local registry/provider boundaries, WHEN real CLI flows run, THEN report safely."""

import os
import socketserver
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import TypedDict

import click
import pytest
from click.testing import CliRunner

from cli.wizard.context import BootstrapContext
from cli.wizard.steps.domain import DomainStep


class ProviderState(TypedDict):
    whois: bytes
    rdap_status: int
    requests: list[str]
    queries: list[bytes]
    prices: bytes
    bootstrap: bytes | None


@pytest.fixture
def providers(monkeypatch):
    # GIVEN real TCP WHOIS and HTTP RDAP/price stand-ins; no CLI code is mocked.
    state: ProviderState = {
        "whois": b"Status: free\r\n",
        "rdap_status": 404,
        "requests": [],
        "queries": [],
        "prices": Path("tests/fixtures/hetzner-domain-prices.json").read_bytes(),
        "bootstrap": None,
    }

    class Whois(socketserver.StreamRequestHandler):
        def handle(self):
            state["queries"].append(self.rfile.readline())
            self.wfile.write(state["whois"])

    class HTTP(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"].append(self.path)
            status = 200
            if self.path == "/bootstrap":
                body = (
                    state["bootstrap"]
                    or (
                        '{"services": [[["com", "uk", "ai", "zip"], ["'
                        + url
                        + '/rdap/"]]]}'
                    ).encode()
                )
            elif self.path.startswith("/rdap/domain/"):
                status = state["rdap_status"]
                body = b'{"objectClassName": "domain"}'
            else:
                body = state["prices"]
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            pass

    whois = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Whois)
    http = ThreadingHTTPServer(("127.0.0.1", 0), HTTP)
    url = f"http://127.0.0.1:{http.server_port}"
    threads = [Thread(target=s.serve_forever, daemon=True) for s in (whois, http)]
    for thread in threads:
        thread.start()
    monkeypatch.setenv("STARTUP_DENIC_WHOIS", f"127.0.0.1:{whois.server_address[1]}")
    monkeypatch.setenv("STARTUP_RDAP_BOOTSTRAP_URL", url + "/bootstrap")
    monkeypatch.setenv("STARTUP_HETZNER_DOMAIN_PRICES_URL", url + "/prices")
    try:
        yield state
    finally:
        for server, thread in zip((whois, http), threads, strict=True):
            server.shutdown()
            server.server_close()
            thread.join()


def cli(*args):
    return subprocess.run(
        [sys.executable, "-m", "cli.startup", "hetzner", "domain-check", *args],
        env={**os.environ, "PYTHONPATH": str(Path("src").resolve()), "NO_COLOR": "1"},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize(
    "status,code,availability",
    [("free", 0, "free"), ("connect", 1, "registered"), ("failed", 2, "unknown")],
)
def test_denic_cli(providers, status, code, availability):
    # GIVEN a DENIC status response.
    providers["whois"] = f"Domain: sample.de\r\nStatus: {status}\r\n".encode()
    # WHEN the real CLI checks exactly one domain.
    result = cli("sample.de")
    # THEN it reports the registry result and a sourced gross standard estimate.
    assert result.returncode == code, result.stderr
    assert f"sample.de: {availability}" in result.stdout
    assert "5.83 EUR/year gross" in result.stdout
    assert "19% German VAT" in result.stdout
    assert "Renewal: price unknown" in result.stdout
    assert "Price source: http://127.0.0.1:" in result.stdout
    assert providers["queries"] == [b"-T dn,ace sample.de\r\n"]


@pytest.mark.parametrize(
    "status,code,availability",
    [
        (404, 0, "free"),
        (200, 1, "registered"),
        (429, 2, "unknown"),
        (503, 2, "unknown"),
    ],
)
def test_rdap_cli(providers, status, code, availability):
    # GIVEN IANA bootstrap and a registry HTTP response.
    providers["rdap_status"] = status
    # WHEN the CLI follows the discovered RDAP URL.
    result = cli("sample.com")
    # THEN only 404 means free, only 200 means registered, and errors stay unknown.
    assert result.returncode == code, result.stderr
    assert f"sample.com: {availability}" in result.stdout
    assert "17.26 EUR/year gross" in result.stdout
    assert providers["requests"] == ["/bootstrap", "/rdap/domain/sample.com", "/prices"]


@pytest.mark.parametrize(
    "value,normalized",
    [
        ("HTTPS://BÜCHER.DE./", "xn--bcher-kva.de"),
        ("Sample.COM.", "sample.com"),
        ("sample.co.uk", "sample.co.uk"),
    ],
)
def test_normalization(providers, value, normalized):
    # GIVEN mixed case, optional scheme/root dot, IDN or a multi-label public suffix.
    # WHEN the real CLI checks the value.
    result = cli(value)
    # THEN normalized ACE goes to the registry and appears in output.
    assert result.returncode == 0, result.stderr
    assert f"{normalized}: free" in result.stdout
    if normalized.endswith(".de"):
        assert providers["queries"] == [f"-T dn,ace {normalized}\r\n".encode()]
    else:
        assert f"/rdap/domain/{normalized}" in providers["requests"]


@pytest.mark.parametrize(
    "value",
    [
        "garbage",
        "127.0.0.1",
        "co.uk",
        "www.example.com",
        "-bad.de",
        "bad_name.de",
        "foo..de",
        "example.de..",
        "https://example.de/path",
        "example.de:43",
        "a\r\n.de",
        "https://sample\n.de",
        "xn--.de",
        "a" * 64 + ".de",
    ],
)
def test_invalid_input_never_contacts_servers(providers, value):
    # GIVEN an invalid or non-registrable input.
    # WHEN the actual CLI validates it.
    result = cli("--", value)
    # THEN it exits helpfully before any provider calls.
    assert result.returncode == 2
    assert "Invalid domain:" in result.stderr
    assert providers["queries"] == []
    assert providers["requests"] == []


@pytest.mark.parametrize(
    "domain,seam",
    [
        ("sample.de", "STARTUP_DENIC_WHOIS"),
        ("sample.com", "STARTUP_RDAP_BOOTSTRAP_URL"),
    ],
)
def test_unreachable_is_unknown(providers, monkeypatch, domain, seam):
    # GIVEN a closed local endpoint.
    with socketserver.TCPServer(
        ("127.0.0.1", 0), socketserver.BaseRequestHandler
    ) as closed:
        port = closed.server_address[1]
    monkeypatch.setenv(
        seam,
        f"127.0.0.1:{port}"
        if domain.endswith(".de")
        else f"http://127.0.0.1:{port}/bootstrap",
    )
    # WHEN a real socket/HTTP connection fails.
    result = cli(domain)
    # THEN no retry or free claim occurs.
    assert result.returncode == 2
    assert f"{domain}: unknown" in result.stdout
    assert "Availability unknown:" in result.stdout
    assert providers["queries"] == []


@pytest.mark.parametrize(
    "payload",
    [
        b"{}",
        b"<html>changed</html>",
        b'{"data":[{"tld":".de","price":"NaN","period":1}]}',
        b'{"data":[{"tld":".de","price":4.9,"period":0}]}',
    ],
)
def test_price_format_changes_do_not_change_availability(providers, payload):
    # GIVEN missing or changed price data.
    providers["prices"] = payload
    # WHEN the real CLI reads it.
    result = cli("sample.de")
    # THEN availability remains free and prices fail clearly without guessed values.
    assert result.returncode == 0
    assert "registration/renewal: price unknown" in result.stdout
    assert "Hetzner shows it before you confirm the order" in result.stdout
    assert "Price lookup failed:" in result.stdout


def test_two_year_price_and_missing_tld(providers):
    # GIVEN the saved live feed, including .ai's two-year period and no .zip rate.
    # WHEN checking both registrable names.
    ai, uk = cli("sample.ai"), cli("sample.zip")
    # THEN yearly comparison and the actual billing period are explicit.
    assert ai.returncode == uk.returncode == 0
    assert "136.85 EUR/year gross" in ai.stdout
    assert "billed every 2 years" in ai.stdout
    assert "TLD missing" in uk.stdout


def test_malformed_bootstrap_is_unknown(providers):
    # GIVEN a changed IANA format.
    providers["bootstrap"] = b'{"services": null}'
    # WHEN it is read by the CLI.
    result = cli("sample.com")
    # THEN no domain lookup is attempted and availability is unknown.
    assert result.returncode == 2
    assert "sample.com: unknown" in result.stdout
    assert providers["requests"] == ["/bootstrap", "/prices"]


def test_help_documents_exit_codes(providers):
    # GIVEN the installed CLI.
    # WHEN viewing help.
    result = cli("--help")
    # THEN all outcomes are documented without provider queries.
    assert result.returncode == 0
    assert "0 = free, 1 = registered, 2 = unknown/error" in result.stdout
    assert providers["requests"] == []


def wizard(tmp_path, kind="fullstack", *, owned=None, unattended=False):
    ctx = BootstrapContext(
        "sample",
        "sample.de",
        "",
        "owner",
        "18",
        "",
        tmp_path,
        kind=kind,
        domain_owned=owned,
        non_interactive=unattended,
        cloudflare_nameservers=["one.example.com", "two.example.com"],
    )

    @click.command()
    def flow():
        step = DomainStep()
        if not step.check(ctx):
            step.run(ctx)

    return flow, ctx


@pytest.fixture
def browser_boundary(monkeypatch):
    import playwright.async_api

    starts = []

    def launch():
        starts.append(True)
        raise RuntimeError("browser boundary reached")

    # Only replace the external browser API, not registration or wizard methods.
    monkeypatch.setattr(playwright.async_api, "async_playwright", launch)
    return starts


@pytest.mark.parametrize("kind,choice", [("fullstack", "2"), ("pitch", "3")])
def test_domain_wizard_taken_stops_before_browser(
    providers, browser_boundary, tmp_path, kind, choice
):
    # GIVEN a taken domain and the real ownership/buy wizard.
    providers["whois"] = b"Status: connect\r\n"
    flow, _ = wizard(tmp_path, kind)
    # WHEN the user chooses to buy in either product flow.
    result = CliRunner().invoke(flow, input=choice + "\n")
    # THEN the flow stops before starting browser automation.
    assert result.exit_code == 1
    assert (
        "already registered; choose another domain or answer that you own it"
        in result.output
    )
    assert browser_boundary == []
    assert len(providers["queries"]) == 1


@pytest.mark.parametrize("kind,choice", [("fullstack", "2"), ("pitch", "3")])
@pytest.mark.parametrize("status", ["free", "failed"])
@pytest.mark.parametrize("answer,starts", [("n", False), ("", True)])
def test_domain_wizard_confirmation_e2e(
    providers, browser_boundary, tmp_path, kind, choice, status, answer, starts
):
    # GIVEN a free or unknown domain and a complete domain wizard through the browser boundary.
    providers["whois"] = f"Status: {status}\r\n".encode()
    flow, ctx = wizard(tmp_path, kind)
    # WHEN the user declines or accepts the default Yes confirmation.
    result = CliRunner().invoke(flow, input=f"{choice}\n{answer}\n")
    # THEN prices/warnings are visible before any external browser is launched.
    assert "5.83 EUR/year gross" in result.output
    assert "Continue to Hetzner" in result.output
    assert bool(browser_boundary) is starts
    if starts:
        assert isinstance(result.exception, RuntimeError)
        assert ctx.base_domain == "sample.de"
    else:
        assert "Cancelled." in result.output
    if status == "failed":
        assert "unknown" in result.output


@pytest.mark.parametrize("owned,answer", [(True, ""), (None, "1\n")])
def test_owned_domain_warns_but_does_not_block(
    providers, browser_boundary, tmp_path, owned, answer
):
    # GIVEN an ownership assertion for a domain reported free.
    flow, _ = wizard(tmp_path, owned=owned)
    # WHEN the fullstack ownership check completes.
    result = CliRunner().invoke(flow, input=answer)
    # THEN it warns and skips buying; price fetches are unnecessary.
    assert result.exit_code == 0, result.output
    assert "does not appear to be registered" in result.output
    assert browser_boundary == []
    assert providers["requests"] == []


def test_owned_pitch_domain_continues_with_other_registrar(
    providers, browser_boundary, tmp_path
):
    # GIVEN a free registry response and another-registrar ownership answer.
    flow, _ = wizard(tmp_path, "pitch")
    # WHEN the user acknowledges real manual nameserver instructions.
    result = CliRunner().invoke(flow, input="2\n\n")
    # THEN the warning does not block instructions or start Hetzner registration.
    assert result.exit_code == 0, result.output
    assert "does not appear to be registered" in result.output
    assert "one.example.com" in result.output
    assert browser_boundary == []


@pytest.mark.parametrize("status,starts", [("connect", False), ("free", True)])
def test_unattended_buy_checks_domain_without_prompting(
    providers, browser_boundary, tmp_path, status, starts
):
    # GIVEN --yes semantics, an explicit --buy-domain and a taken or free domain.
    providers["whois"] = f"Status: {status}\r\n".encode()
    flow, _ = wizard(tmp_path, owned=False, unattended=True)
    # WHEN the wizard runs with no input.
    result = CliRunner().invoke(flow)
    # THEN a taken domain stops before the browser; a free one shows its price
    # and continues to Hetzner's order, where the user confirms, without a prompt.
    assert "Continue to Hetzner" not in result.output
    assert bool(browser_boundary) is starts
    if starts:
        assert "5.83 EUR/year gross" in result.output
        assert isinstance(result.exception, RuntimeError)
    else:
        assert result.exit_code == 1
        assert "already registered" in result.output
