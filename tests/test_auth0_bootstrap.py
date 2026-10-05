from subprocess import CompletedProcess

import click
import pytest

from cli import auth0_commands as auth


def test_auth0_credentials_stay_in_stdin_and_cookie_survives_resume(
    monkeypatch, tmp_path
):
    (tmp_path / "oauth2-proxy" / "deployment").mkdir(parents=True)
    calls = []
    responses = iter(
        [
            [],
            {"client_id": "client-id", "client_secret": "private-client-secret"},
            [{"identifier": "https://demo.example/private_api"}],
        ]
    )
    monkeypatch.setattr(auth, "auth0_json", lambda *args: next(responses))

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if "get-field" in command:
            return CompletedProcess(
                command,
                0 if command[-1] == "oauth2_proxy_cookie_secret" else 1,
                "existing-cookie"
                if command[-1] == "oauth2_proxy_cookie_secret"
                else "",
                "",
            )
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(auth.subprocess, "run", run)
    auth.configure_auth0(tmp_path, "demo", "demo.example", "tenant.eu.auth0.com")
    updates = [(cmd, options) for cmd, options in calls if "update" in cmd]
    secret_updates = [
        (cmd, options)
        for cmd, options in updates
        if options["input"] == "private-client-secret"
    ]
    assert len(secret_updates) == 2
    assert "--dry-run" in secret_updates[0][0]
    assert all("private-client-secret" not in cmd for cmd, _ in calls)
    assert not any("oauth2_proxy_cookie_secret" in cmd for cmd, _ in updates)


def test_auth0_failure_does_not_print_provider_secrets(monkeypatch):
    monkeypatch.setattr(
        auth.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(
            args[0], 1, "private-client-secret", "private-client-secret"
        ),
    )
    with pytest.raises(click.ClickException) as error:
        auth.auth0_json("apps", "create")
    assert "private-client-secret" not in str(error.value)


def test_auth0_step_precedes_first_push_only_when_requested():
    from pathlib import Path

    from cli.wizard.context import BootstrapContext
    from cli.wizard.runner import steps_for
    from cli.wizard.steps.auth0 import Auth0Step
    from cli.wizard.steps.finalize import FinalizeStep

    ctx = BootstrapContext(
        project_name="demo",
        base_domain="demo.example",
        additional_domains="",
        github_username="owner",
        postgres_version="17",
        sentry_dsn="",
        output_dir=Path("."),
        auth0_tenant="tenant",
    )
    assert steps_for(ctx)[-2:] == [Auth0Step, FinalizeStep]
    ctx.auth0_tenant = None
    assert Auth0Step not in steps_for(ctx)
