"""Read-only, single-domain registry checks; external endpoints are test seams."""

from __future__ import annotations

import os
import re
import socket
import time
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

import httpx
import tldextract

from .domain_prices import domain_price

TIMEOUT = 8.0
BOOTSTRAP_URL = "https://data.iana.org/rdap/dns.json"
# Use the packaged ICANN suffix snapshot, without network requests or disk cache.
SUFFIXES = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)


def normalize_domain(value: str) -> str:
    """Accept a bare registrable domain or an http(s) URL containing only its host."""
    value = value.strip().lower()
    if any(character.isspace() for character in value):
        raise ValueError("Invalid domain; domain names cannot contain whitespace.")
    if "://" in value:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Enter a domain such as example.com, without a path or query."
            )
        value = parsed.netloc
    if value.endswith("."):
        value = value[:-1]
    try:
        domain = value.encode("idna").decode("ascii")
        # Reject malformed ACE input as well as invalid ASCII labels.
        domain.encode("ascii").decode("idna")
    except UnicodeError as exc:
        raise ValueError(
            "Invalid international domain name; enter e.g. example.com."
        ) from exc
    labels = domain.split(".")
    if len(domain) > 253 or any(
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
        for label in labels
    ):
        raise ValueError(
            "Invalid domain; enter a registrable name such as example.com."
        )
    parts = SUFFIXES(domain)
    if not parts.suffix or not parts.domain or parts.subdomain:
        raise ValueError(
            "Enter a registrable domain such as example.com or example.co.uk, "
            "not an IP address, public suffix or subdomain."
        )
    return domain


@dataclass(frozen=True)
class DomainCheck:
    domain: str
    availability: Literal["free", "registered", "unknown"]
    source: str
    detail: str = ""
    price: str = ""

    @property
    def exit_code(self) -> int:
        return {"free": 0, "registered": 1, "unknown": 2}[self.availability]

    def output(self) -> str:
        lines = [
            f"{self.domain}: {self.availability}",
            f"Availability source: {self.source}",
        ]
        if self.detail:
            lines.append(self.detail)
        lines.append(self.price)
        return "\n".join(lines)


def check_domain(value: str, *, include_price: bool = True) -> DomainCheck:
    """Check one registrable domain. Registry failures never imply availability."""
    domain = normalize_domain(value)
    source = ""
    availability: Literal["free", "registered", "unknown"] = "unknown"
    detail = ""
    try:
        if domain.endswith(".de"):
            endpoint = os.environ.get("STARTUP_DENIC_WHOIS", "whois.denic.de:43")
            source = f"DENIC WHOIS ({endpoint})"
            host, port = endpoint.rsplit(":", 1)
            if not 1 <= int(port) <= 65535:
                raise ValueError("WHOIS port must be between 1 and 65535")
            deadline = time.monotonic() + TIMEOUT
            with socket.create_connection((host, int(port)), timeout=TIMEOUT) as conn:
                conn.sendall(f"-T dn,ace {domain}\r\n".encode("ascii"))
                chunks = bytearray()
                while len(chunks) < 128 * 1024:
                    conn.settimeout(max(0.001, deadline - time.monotonic()))
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    chunks.extend(chunk)
                else:
                    raise ValueError("WHOIS response exceeded size limit")
            statuses = re.findall(
                r"^Status:\s*(\S+)\s*$", chunks.decode("utf-8", errors="replace"), re.M
            )
            if statuses == ["free"]:
                availability = "free"
            elif statuses == ["connect"]:
                availability = "registered"
            else:
                detail = "DENIC returned an unrecognised status; availability unknown."
        else:
            bootstrap = os.environ.get("STARTUP_RDAP_BOOTSTRAP_URL", BOOTSTRAP_URL)
            source = f"IANA RDAP bootstrap ({bootstrap})"
            with httpx.Client(timeout=TIMEOUT, follow_redirects=True) as client:
                response = client.get(bootstrap)
                response.raise_for_status()
                services = response.json()["services"]
                bases = next(
                    (
                        urls
                        for tlds, urls in services
                        if domain.rsplit(".", 1)[1] in tlds
                    ),
                    [],
                )
                if not bases:
                    raise ValueError("No RDAP service listed for this TLD")
                if not isinstance(bases, list) or any(
                    not isinstance(url, str) for url in bases
                ):
                    raise ValueError("Invalid RDAP service URLs in the bootstrap file")
                base = next(
                    (url for url in bases if url.startswith("https://")), bases[0]
                )
                if not base.startswith(("http://", "https://")):
                    raise ValueError("Invalid RDAP service URL")
                source = base.rstrip("/") + "/domain/" + domain
                response = client.get(
                    source, headers={"Accept": "application/rdap+json"}
                )
                if response.status_code == 404:
                    availability = "free"
                elif response.status_code == 200:
                    availability = "registered"
                else:
                    detail = f"RDAP returned HTTP {response.status_code}; availability unknown."
    except (
        OSError,
        httpx.HTTPError,
        httpx.InvalidURL,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
    ) as exc:
        detail = f"Availability unknown: {exc}"
    return DomainCheck(
        domain,
        availability,
        source,
        detail,
        domain_price(domain) if include_price else "",
    )
