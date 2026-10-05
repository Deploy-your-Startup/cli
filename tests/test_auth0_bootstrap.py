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


def test_template_authentication_requires_explicit_choice_before_resources(
    monkeypatch, tmp_path
):
    from cli.wizard.context import BootstrapContext
    from cli.wizard.runner import run_wizard

    ctx = BootstrapContext(
        "demo", "demo.example", "", "owner", "17", "", tmp_path, non_interactive=True
    )
    monkeypatch.setattr(
        "cli.template_commands.template_authentication", lambda *args: "auth0"
    )
    with pytest.raises(click.ClickException, match=r"--auth0-tenant.*--without-auth"):
        run_wizard(ctx)
    assert not ctx.project_dir.exists()


def test_expired_auth0_login_stops_before_cloud_resources(monkeypatch, tmp_path):
    from cli.wizard.context import BootstrapContext
    from cli.wizard.runner import run_wizard

    ctx = BootstrapContext(
        "demo",
        "demo.example",
        "",
        "owner",
        "17",
        "",
        tmp_path,
        auth0_tenant="tenant.eu.auth0.com",
    )
    monkeypatch.setattr(auth.shutil, "which", lambda name: name)
    monkeypatch.setattr(
        auth.subprocess,
        "run",
        lambda *args, **kwargs: CompletedProcess(
            args[0], 1, "", "expired private token"
        ),
    )
    with pytest.raises(click.ClickException, match="auth0 login"):
        run_wizard(ctx)
    assert not ctx.project_dir.exists()


def test_auth0_preflight_checks_app_and_api_access(monkeypatch):
    calls = []
    monkeypatch.setattr(auth.shutil, "which", lambda name: name)
    monkeypatch.setattr(auth, "auth0_json", lambda *args: calls.append(args))
    auth.check_auth0_login("tenant.eu.auth0.com")
    assert calls == [
        ("apps", "list", "--tenant", "tenant.eu.auth0.com"),
        ("apis", "list", "--tenant", "tenant.eu.auth0.com"),
    ]


@pytest.mark.parametrize(
    "private_status,http_only,secure,passes",
    [
        (200, True, True, True),
        (404, True, True, False),
        (200, False, True, False),
        (200, True, False, False),
    ],
)
def test_live_login_requires_private_api_and_safe_session(
    private_status, http_only, secure, passes
):
    from types import SimpleNamespace

    context = SimpleNamespace(
        request=SimpleNamespace(
            get=lambda url, **kwargs: SimpleNamespace(
                status=private_status if "/private_api" in url else 200
            )
        ),
        cookies=lambda urls: [
            {
                "name": "_oauth2_proxy",
                "httpOnly": http_only,
                "secure": secure,
                "value": "private-session",
            }
        ],
    )
    if passes:
        auth.validate_browser_session(context, "https://demo.example")
    else:
        with pytest.raises(click.ClickException) as error:
            auth.validate_browser_session(context, "https://demo.example")
        assert "private-session" not in str(error.value)
