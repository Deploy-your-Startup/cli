"""Checks for project-scoped Hetzner token validation."""

from types import SimpleNamespace

from cli.wizard.steps import hetzner


def test_validate_hetzner_token_uses_project_scoped_servers_endpoint(monkeypatch):
    requests = []

    def fake_get(url, **kwargs):
        requests.append((url, kwargs))
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(hetzner.httpx, "get", fake_get)

    assert hetzner.validate_hetzner_token("test-token") is True
    assert requests == [
        (
            "https://api.hetzner.cloud/v1/servers",
            {
                "headers": {"Authorization": "Bearer test-token"},
                "params": {"per_page": 1},
                "timeout": 10,
            },
        )
    ]


def test_validate_hetzner_token_rejects_unauthorized_response(monkeypatch):
    monkeypatch.setattr(
        hetzner.httpx,
        "get",
        lambda *_args, **_kwargs: SimpleNamespace(status_code=401),
    )

    assert hetzner.validate_hetzner_token("invalid-token") is False
