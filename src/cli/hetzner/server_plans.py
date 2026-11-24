"""Rank currently available Hetzner plans using the project's gross prices."""

from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

import click
import httpx

EU_LOCATIONS = {"fsn1", "nbg1", "hel1"}


def _price_location(price: dict) -> str:
    if "location" in price:
        return price["location"]
    return str(price.get("datacenter", "")).split("-")[0]


def monthly_price(
    pricing: dict, category: str, name: str, location: str
) -> tuple[Decimal, Decimal] | None:
    key = "type" if category == "primary_ips" else "name"
    try:
        monthly = next(
            (
                price["price_monthly"]
                for entry in pricing.get(category, [])
                if entry.get(key) == name
                for price in entry.get("prices", [])
                if _price_location(price) == location
            ),
            None,
        )
        if monthly is None:
            return None
        net, gross = Decimal(monthly["net"]), Decimal(monthly["gross"])
        return (
            (net, gross)
            if all(p.is_finite() and p >= 0 for p in (net, gross))
            else None
        )
    except (KeyError, TypeError, ValueError, InvalidOperation, AttributeError):
        return None


@dataclass(frozen=True, order=True)
class ServerPlan:
    gross: Decimal
    server_type: str
    location: str


def _catalogue(api_url: str, token: str) -> Iterator[dict[str, Any]]:
    try:
        page = 1
        with httpx.Client(
            headers={"Authorization": f"Bearer {token}"}, timeout=10
        ) as client:
            while True:
                response = client.get(
                    f"{api_url}/server_types", params={"per_page": 50, "page": page}
                )
                response.raise_for_status()
                body = response.json()
                yield from body["server_types"]
                next_page = body.get("meta", {}).get("pagination", {}).get("next_page")
                if next_page is None:
                    return
                if not isinstance(next_page, int) or next_page <= page:
                    raise ValueError("invalid catalogue pagination")
                page = next_page
    except (httpx.HTTPError, KeyError, ValueError, TypeError, AttributeError):
        raise click.ClickException(
            "Could not verify Hetzner server availability. Reconnect Hetzner and retry bootstrap."
        ) from None


def require_empty_project(api_url: str, token: str) -> None:
    try:
        response = httpx.get(
            f"{api_url}/servers",
            headers={"Authorization": f"Bearer {token}"},
            params={"per_page": 1},
            timeout=10,
        )
        response.raise_for_status()
        servers = response.json()["servers"]
        if not isinstance(servers, list):
            raise ValueError("invalid server list")
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        raise click.ClickException(
            "Could not verify that the Hetzner project is empty."
        ) from None
    if servers:
        raise click.ClickException(
            "--cheapest-available requires a Hetzner project without existing servers."
        )


def _available_locations(server: dict, locations: set[str]) -> Iterator[str]:
    for location in server.get("locations", []):
        name = location["name"]
        if (
            name in locations
            and location.get("available") is True
            and not location.get("deprecation")
        ):
            yield name


def _eligible(server: dict) -> bool:
    return (
        server.get("architecture") == "x86"
        and server.get("cpu_type") == "shared"
        and server.get("cores", 0) >= 2
        and server.get("memory", 0) >= 4
    )


def cheapest_plan(
    api_url: str, token: str, pricing: dict, location: str | None
) -> ServerPlan:
    locations = {location} & EU_LOCATIONS if location else EU_LOCATIONS
    plans = []
    try:
        for server in _catalogue(api_url, token):
            if not _eligible(server):
                continue
            for region in _available_locations(server, locations):
                name = server["name"]
                price = monthly_price(pricing, "server_types", name, region)
                ipv4 = monthly_price(pricing, "primary_ips", "ipv4", region)
                if price is not None and ipv4 is not None:
                    plans.append(ServerPlan(price[1] + ipv4[1], name, region))
    except (KeyError, TypeError, AttributeError):
        raise click.ClickException(
            "Hetzner returned an invalid server catalogue."
        ) from None
    if not plans:
        raise click.ClickException(
            "No available EU Hetzner plan with a verified price, 2 x86 vCPUs and 4 GB RAM."
        )
    return min(plans)
