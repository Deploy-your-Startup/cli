from types import SimpleNamespace

from cli import bootstrap


def test_ensure_ghcr_scopes_accepts_write_packages(monkeypatch):
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        return SimpleNamespace(
            stdout="Token scopes: 'repo', 'write:packages'", stderr=""
        )

    monkeypatch.setattr(bootstrap.subprocess, "run", fake_run)

    bootstrap._ensure_ghcr_scopes()

    assert calls == [["gh", "auth", "status"], ["gh", "auth", "status"]]
