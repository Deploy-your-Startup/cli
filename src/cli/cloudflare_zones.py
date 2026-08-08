"""Cloudflare zone & Pages custom-domain helpers (direct REST API via httpx).

Used by the pitch flow to make Cloudflare the DNS authority for a domain and to
attach the domain to the Pages project. Mirrors the httpx pattern in
``wizard/steps/pitch_finalize.py`` (no SDK, no wrangler).
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

API_BASE = "https://api.cloudflare.com/client/v4"
TIMEOUT = 20


@dataclass
class ZoneInfo:
    """Result of ensuring a Cloudflare zone exists."""

    zone_id: str
    name: str  # the zone's apex domain (may differ from the requested subdomain)
    nameservers: list[str]
    status: str  # "active", "pending", ...
    created: bool  # True if we just created it, False if it already existed


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _zone_from_result(zone: dict, *, created: bool) -> ZoneInfo:
    return ZoneInfo(
        zone_id=zone["id"],
        name=zone.get("name", ""),
        nameservers=zone.get("name_servers", []),
        status=zone.get("status", "unknown"),
        created=created,
    )


def _find_parent_zone(
    headers: dict[str, str], account_id: str, domain: str
) -> dict | None:
    """Return the existing account zone that ``domain`` is a subdomain of.

    Cloudflare refuses to create a zone for a subdomain (error 1116); such hosts
    live as DNS records inside their registrable root zone. We list the account's
    zones and pick the longest zone name that ``domain`` ends with — e.g.
    ``app.example.com`` resolves to the ``example.com`` zone.
    """
    resp = httpx.get(
        f"{API_BASE}/zones",
        headers=headers,
        params={"account.id": account_id, "per_page": 50},
        timeout=TIMEOUT,
    )
    data = resp.json()
    if resp.status_code != 200 or not data.get("success"):
        return None

    candidates = [
        zone
        for zone in data.get("result", [])
        if domain == zone["name"] or domain.endswith(f".{zone['name']}")
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda z: len(z["name"]))


def ensure_zone(token: str, account_id: str, domain: str) -> ZoneInfo:
    """Return the Cloudflare zone responsible for ``domain``, creating it if missing.

    If ``domain`` is an apex/root domain its own zone is created (or reused) and
    the returned ``nameservers`` must be set at the registrar. If ``domain`` is a
    subdomain of a zone already in the account, that parent zone is reused — the
    subdomain itself is served via DNS records, not a separate zone. Idempotent.
    """
    headers = _headers(token)

    get_resp = httpx.get(
        f"{API_BASE}/zones",
        headers=headers,
        params={"name": domain, "account.id": account_id},
        timeout=TIMEOUT,
    )
    data = get_resp.json()
    if get_resp.status_code == 200 and data.get("success") and data.get("result"):
        return _zone_from_result(data["result"][0], created=False)

    # A subdomain cannot be its own zone — reuse the existing root zone if present.
    parent = _find_parent_zone(headers, account_id, domain)
    if parent is not None:
        return _zone_from_result(parent, created=False)

    create_resp = httpx.post(
        f"{API_BASE}/zones",
        headers=headers,
        json={"name": domain, "account": {"id": account_id}, "type": "full"},
        timeout=TIMEOUT,
    )
    cdata = create_resp.json()
    if create_resp.status_code in {200, 201} and cdata.get("success"):
        return _zone_from_result(cdata["result"], created=True)

    raise RuntimeError(f"Cloudflare-Zone konnte nicht angelegt werden: {cdata}")


def ensure_cname_record(
    token: str, zone_id: str, name: str, target: str, proxied: bool = True
) -> bool:
    """Create a (proxied) CNAME ``name`` → ``target`` if it does not exist yet.

    Attaching a domain to Pages via the API does NOT create the DNS record
    (the dashboard does that for in-account zones). Pages stays "Requires DNS
    setup" until this record exists. Returns True if a record was created,
    False if an equivalent one was already present. Idempotent.
    """
    headers = _headers(token)
    want = target.rstrip(".")

    resp = httpx.get(
        f"{API_BASE}/zones/{zone_id}/dns_records",
        headers=headers,
        params={"name": name, "type": "CNAME"},
        timeout=TIMEOUT,
    )
    data = resp.json()
    if resp.status_code == 200 and data.get("success"):
        for record in data.get("result", []):
            if str(record.get("content", "")).rstrip(".") == want:
                return False

    create = httpx.post(
        f"{API_BASE}/zones/{zone_id}/dns_records",
        headers=headers,
        json={
            "type": "CNAME",
            "name": name,
            "content": target,
            "proxied": proxied,
            "ttl": 1,  # 1 = automatic
        },
        timeout=TIMEOUT,
    )
    cdata = create.json()
    if create.status_code in {200, 201} and cdata.get("success"):
        return True

    raise RuntimeError(
        f"CNAME-Record konnte nicht angelegt werden ({name} → {target}): {cdata}"
    )


def add_pages_custom_domain(
    token: str, account_id: str, project: str, domain: str
) -> bool:
    """Attach ``domain`` as a custom domain to a Cloudflare Pages project.

    Idempotent: returns True if the domain is now attached, including the case
    where it was already attached. When the zone lives in the same account,
    Cloudflare auto-provisions the DNS record and TLS certificate.
    """
    headers = _headers(token)
    url = f"{API_BASE}/accounts/{account_id}/pages/projects/{project}/domains"

    resp = httpx.post(url, headers=headers, json={"name": domain}, timeout=TIMEOUT)
    data = resp.json()
    if resp.status_code in {200, 201} and data.get("success"):
        return True

    # Already attached → treat as success (idempotent).
    if _already_exists(resp.status_code, data) or _domain_attached(
        token, account_id, project, domain
    ):
        return True

    raise RuntimeError(f"Custom Domain konnte nicht mit Pages verknüpft werden: {data}")


def _already_exists(status_code: int, data: dict) -> bool:
    if status_code not in {400, 409}:
        return False
    for err in data.get("errors", []) or []:
        message = str(err.get("message", "")).lower()
        if "already" in message or "exist" in message:
            return True
    return False


def _domain_attached(token: str, account_id: str, project: str, domain: str) -> bool:
    url = f"{API_BASE}/accounts/{account_id}/pages/projects/{project}/domains/{domain}"
    try:
        resp = httpx.get(url, headers=_headers(token), timeout=TIMEOUT)
        return resp.status_code == 200 and resp.json().get("success", False)
    except httpx.HTTPError:
        return False


# Record types that would block a Pages custom domain from owning a hostname.
_CONFLICTING_TYPES = {"A", "AAAA", "CNAME"}


def clear_conflicting_records(token: str, zone_id: str, names: list[str]) -> int:
    """Delete A/AAAA/CNAME records for ``names`` so Pages can own those hosts.

    When a zone is created on Cloudflare, existing records are auto-imported.
    For a pitch site those apex/www records (pointing at the old host) must be
    removed before the Pages custom domain can take over. Returns the number of
    deleted records. Idempotent: deletes nothing when the records are gone.
    """
    headers = _headers(token)
    deleted = 0
    for name in names:
        resp = httpx.get(
            f"{API_BASE}/zones/{zone_id}/dns_records",
            headers=headers,
            params={"name": name},
            timeout=TIMEOUT,
        )
        data = resp.json()
        if resp.status_code != 200 or not data.get("success"):
            continue
        for record in data.get("result", []):
            if record.get("type") not in _CONFLICTING_TYPES:
                continue
            del_resp = httpx.delete(
                f"{API_BASE}/zones/{zone_id}/dns_records/{record['id']}",
                headers=headers,
                timeout=TIMEOUT,
            )
            if del_resp.status_code == 200:
                deleted += 1
    return deleted
