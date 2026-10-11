"""Parse the public KonsoleH TLD rate feed used by Hetzner's whois page.

The feed's price is net EUR per billing period. The page's whois.js adds VAT.
It does not identify renewal or premium-domain prices: never invent those.
"""

from __future__ import annotations

import os
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import httpx
import tldextract

PRICE_URL = "https://www.hetzner.com/_resources/app/data/app/live_data_whois_EUR.json"
UNKNOWN = "price unknown — Hetzner shows it before you confirm the order"
SUFFIXES = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)


def domain_price(domain: str) -> str:
    """Return a sourced standard registration estimate, or an explicit unknown."""
    url = os.environ.get("STARTUP_HETZNER_DOMAIN_PRICES_URL", PRICE_URL)
    try:
        # A client retains Hetzner's cookie challenge across redirects.
        with httpx.Client(timeout=8.0, follow_redirects=True) as client:
            response = client.get(url)
            response.raise_for_status()
            data = response.json()["data"]
        suffix = "." + SUFFIXES(domain).suffix
        rows = [row for row in data if row["tld"] == suffix]
        if len(rows) != 1:
            raise ValueError("TLD missing or duplicated in the price feed")
        row = rows[0]
        period = row["period"]
        if type(period) is not int or period not in {1, 2}:
            raise ValueError("Unsupported billing period in the price feed")
        net = Decimal(str(row["price"]))
        if not net.is_finite() or net <= 0:
            raise ValueError("Invalid price in the price feed")
        gross = (net * Decimal("1.19") / period).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        billing = "" if period == 1 else f"; billed every {period} years"
        return (
            f"Hetzner registration: {gross} EUR/year gross "
            f"(standard TLD estimate, includes 19% German VAT{billing}).\n"
            f"Renewal: {UNKNOWN} (source has no separate renewal price).\n"
            "Premium domains and your tax location may change the order price.\n"
            f"Price source: {url}"
        )
    except (
        httpx.HTTPError,
        httpx.InvalidURL,
        ValueError,
        KeyError,
        TypeError,
        InvalidOperation,
    ) as exc:
        return f"Hetzner registration/renewal: {UNKNOWN}.\nPrice source: {url}\nPrice lookup failed: {exc}"
