"""Tests for cli.cloudflare_zones (zone + Pages custom-domain helpers)."""

from unittest.mock import MagicMock, patch

import pytest

from cli import cloudflare_zones as cz


def _resp(status_code: int, payload: dict) -> MagicMock:
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = payload
    return r


# ── ensure_zone ──────────────────────────────────────────────────────


def test_ensure_zone_reuses_existing():
    existing = _resp(
        200,
        {
            "success": True,
            "result": [
                {
                    "id": "zone123",
                    "name_servers": ["dana.ns.cloudflare.com", "rob.ns.cloudflare.com"],
                    "status": "active",
                }
            ],
        },
    )
    with patch("cli.cloudflare_zones.httpx.get", return_value=existing) as mock_get, \
         patch("cli.cloudflare_zones.httpx.post") as mock_post:
        info = cz.ensure_zone("tok", "acc", "example.com")

    assert info.zone_id == "zone123"
    assert info.nameservers == ["dana.ns.cloudflare.com", "rob.ns.cloudflare.com"]
    assert info.created is False
    mock_get.assert_called_once()
    mock_post.assert_not_called()


def test_ensure_zone_creates_when_missing():
    empty = _resp(200, {"success": True, "result": []})
    created = _resp(
        201,
        {
            "success": True,
            "result": {
                "id": "zoneNEW",
                "name_servers": ["a.ns.cloudflare.com", "b.ns.cloudflare.com"],
                "status": "pending",
            },
        },
    )
    with patch("cli.cloudflare_zones.httpx.get", return_value=empty), \
         patch("cli.cloudflare_zones.httpx.post", return_value=created) as mock_post:
        info = cz.ensure_zone("tok", "acc", "example.com")

    assert info.zone_id == "zoneNEW"
    assert info.created is True
    assert info.status == "pending"
    mock_post.assert_called_once()


def test_ensure_zone_raises_on_failure():
    empty = _resp(200, {"success": True, "result": []})
    failed = _resp(403, {"success": False, "errors": [{"message": "no permission"}]})
    with patch("cli.cloudflare_zones.httpx.get", return_value=empty), \
         patch("cli.cloudflare_zones.httpx.post", return_value=failed):
        with pytest.raises(RuntimeError):
            cz.ensure_zone("tok", "acc", "example.com")


# ── add_pages_custom_domain ──────────────────────────────────────────


def test_add_pages_custom_domain_success():
    ok = _resp(200, {"success": True, "result": {"name": "example.com"}})
    with patch("cli.cloudflare_zones.httpx.post", return_value=ok) as mock_post:
        assert cz.add_pages_custom_domain("tok", "acc", "proj", "example.com") is True
    mock_post.assert_called_once()


def test_add_pages_custom_domain_already_exists_is_idempotent():
    conflict = _resp(
        409, {"success": False, "errors": [{"message": "Domain already exists"}]}
    )
    with patch("cli.cloudflare_zones.httpx.post", return_value=conflict):
        assert cz.add_pages_custom_domain("tok", "acc", "proj", "example.com") is True


def test_add_pages_custom_domain_falls_back_to_get_check():
    # POST fails with a generic 400, but the domain is in fact attached.
    bad = _resp(400, {"success": False, "errors": [{"message": "bad request"}]})
    attached = _resp(200, {"success": True, "result": {"name": "example.com"}})
    with patch("cli.cloudflare_zones.httpx.post", return_value=bad), \
         patch("cli.cloudflare_zones.httpx.get", return_value=attached):
        assert cz.add_pages_custom_domain("tok", "acc", "proj", "example.com") is True


def test_add_pages_custom_domain_raises_when_truly_failed():
    bad = _resp(500, {"success": False, "errors": [{"message": "server error"}]})
    not_found = _resp(404, {"success": False})
    with patch("cli.cloudflare_zones.httpx.post", return_value=bad), \
         patch("cli.cloudflare_zones.httpx.get", return_value=not_found):
        with pytest.raises(RuntimeError):
            cz.add_pages_custom_domain("tok", "acc", "proj", "example.com")


# ── clear_conflicting_records ────────────────────────────────────────


def test_clear_conflicting_records_deletes_only_conflicting_types():
    listing = _resp(
        200,
        {
            "success": True,
            "result": [
                {"id": "rec_a", "type": "A"},
                {"id": "rec_txt", "type": "TXT"},  # must be kept
                {"id": "rec_cname", "type": "CNAME"},
            ],
        },
    )
    empty = _resp(200, {"success": True, "result": []})
    deleted_ok = _resp(200, {"success": True})
    with patch("cli.cloudflare_zones.httpx.get", side_effect=[listing, empty]), \
         patch("cli.cloudflare_zones.httpx.delete", return_value=deleted_ok) as mock_del:
        n = cz.clear_conflicting_records("tok", "zone1", ["example.com", "www.example.com"])

    assert n == 2  # A + CNAME deleted, TXT kept
    deleted_ids = [c.args[0].rsplit("/", 1)[-1] for c in mock_del.call_args_list]
    assert set(deleted_ids) == {"rec_a", "rec_cname"}


def test_clear_conflicting_records_noop_when_empty():
    empty = _resp(200, {"success": True, "result": []})
    with patch("cli.cloudflare_zones.httpx.get", return_value=empty), \
         patch("cli.cloudflare_zones.httpx.delete") as mock_del:
        n = cz.clear_conflicting_records("tok", "zone1", ["example.com"])
    assert n == 0
    mock_del.assert_not_called()
