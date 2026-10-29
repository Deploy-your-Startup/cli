from subprocess import CompletedProcess

import click
import pytest

from cli import auth0_commands as auth


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


def test_failed_auth0_access_starts_auth0_login_in_a_terminal(monkeypatch):
    # GIVEN a terminal whose Auth0 login has expired.
    calls, logged_in = [], []

    def run(command, **kwargs):
        calls.append(command)
        if command == ["auth0", "login"]:
            logged_in.append(True)
            return CompletedProcess(command, 0)
        return CompletedProcess(command, 0 if logged_in else 1, "[]", "")

    monkeypatch.setattr(auth.shutil, "which", lambda name: name)
    monkeypatch.setattr(auth.subprocess, "run", run)
    monkeypatch.setattr(auth.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(auth.sys.stderr, "isatty", lambda: True)

    # WHEN access is checked, THEN `auth0 login` runs once and access is retried.
    auth.check_auth0_login("tenant.eu.auth0.com")
    assert calls.count(["auth0", "login"]) == 1
    assert calls[-1][:3] == ["auth0", "apis", "list"]
