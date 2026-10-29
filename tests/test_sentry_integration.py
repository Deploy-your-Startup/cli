"""GIVEN a personal Sentry token, WHEN `startup sentry setup` runs for a project,
THEN it creates or reuses the Sentry project and vaults the DSN without ever
printing the token or the DSN.

Sentry is replaced at its boundary by two local HTTP servers: a control host
that names a separate region host, as sentry.io does for EU organizations.
The CLI, Ansible Vault and the Keychain stand-in's file store are real; the
login scenarios run the CLI on a pseudo-terminal with a recording browser.
"""

import json
import stat
import textwrap
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from tests.test_one_password_integration import startup
from tests.test_secret_input_integration import argv_log, cli_in_terminal
from tests.test_vault_handoff_integration import ENTRY, PASSWORD, keychain_stand_in
from tests.vault_support import RealVault

TOKEN = "sntryu_synthetic-token"
ORG_TOKEN = "sntrys_synthetic-org-token"
SCOPES = ["org:read", "team:read", "project:read", "project:admin"]
DSN = "https://synthetic-public-key@o1.ingest.de.sentry.io/42"


@dataclass
class FakeSentry:
    projects: set[str]
    calls: list[tuple] = field(default_factory=list)
    status: int = 200
    url: str = ""
    scopes: dict[str, list[str]] = field(
        default_factory=lambda: {TOKEN: SCOPES, ORG_TOKEN: ["org:ci"]}
    )


def serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.fixture
def sentry(request):
    """Return the state of a fake control host and region host."""
    state = FakeSentry(set(request.param))

    class Region(BaseHTTPRequestHandler):
        def reply(self, body, status=200):
            self.send_response(state.status if status == 200 else status)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_GET(self):
            state.calls.append(("GET", self.path, self.headers["Authorization"]))
            parts = self.path.strip("/").split("/")[2:]
            if parts[-1] == "teams":
                self.reply([{"slug": "acme"}])
            elif parts[0] == "organizations":
                self.reply([])
            elif parts[2] not in state.projects:
                self.reply({"detail": "not found"}, 404)
            elif parts[-1] == "keys":
                self.reply([{"isActive": True, "dsn": {"public": DSN}}])
            else:
                self.reply({"slug": parts[2]})

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            state.calls.append(("POST", self.path, body))
            state.projects.add(body["slug"])
            self.reply({"slug": body["slug"]}, 201)

        def log_message(self, format, *args):
            pass

    region = serve(Region)

    class Control(Region):
        def do_GET(self):
            state.calls.append(("CONTROL", self.path, None))
            token = self.headers["Authorization"].removeprefix("Bearer ")
            if self.path == "/api/0/":
                if token not in state.scopes:
                    return self.reply({"detail": "Invalid token"}, 401)
                return self.reply({"auth": {"scopes": state.scopes[token]}})
            if self.path == "/api/0/organizations/":
                return self.reply([{"slug": "acme"}])
            region_url = f"http://127.0.0.1:{region.server_port}"
            self.reply({"slug": "acme", "links": {"regionUrl": region_url}})

    control = serve(Control)
    state.url = f"http://127.0.0.1:{control.server_port}"
    yield state
    control.shutdown()
    region.shutdown()


def project_with_empty_dsn(tmp_path):
    vaulted = RealVault(PASSWORD).encrypt(b"").decode()
    group_vars = tmp_path / "demo-app" / "deployment" / "group_vars"
    group_vars.mkdir(parents=True)
    (group_vars / "all.yml").write_text(
        "project_name: demo-app\nbackend_sentry_dsn: !vault |\n"
        + textwrap.indent(vaulted, "  ")
    )
    return tmp_path / "demo-app"


def sentry_env(tmp_path, url, token=TOKEN):
    env, _ = keychain_stand_in(tmp_path)
    (tmp_path / "keychain" / ENTRY).write_text(PASSWORD)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".sentryclirc").write_text(
        f"[auth]\ntoken={token}\n\n[defaults]\nurl={url}\norg=acme\n"
    )
    env["HOME"] = str(home)
    for name in ("SENTRY_AUTH_TOKEN", "SENTRY_URL", "SENTRY_ORG"):
        env.pop(name, None)
    return env


def setup(env, project):
    return startup(
        env,
        project,
        "sentry",
        "setup",
        "--project-name",
        "demo-app",
        "--project-dir",
        str(project),
    )


def vaulted_dsn(env, project):
    return startup(
        env,
        project,
        "secrets",
        "get-field",
        "-f",
        "deployment/group_vars/all.yml",
        "--field",
        "backend_sentry_dsn",
    ).stdout.strip()


@pytest.mark.parametrize("sentry", [()], indirect=True)
def test_setup_creates_the_project_in_the_region_and_vaults_its_dsn(tmp_path, sentry):
    # GIVEN a project whose vault holds an empty DSN and a login for an
    # organization that lives on a separate region host.
    project = project_with_empty_dsn(tmp_path)
    env = sentry_env(tmp_path, sentry.url)

    # WHEN Sentry is set up with the organization from ~/.sentryclirc.
    result = setup(env, project)

    # THEN the Django project is created in the only team and its DSN is in
    # the vault; neither the token nor the DSN appears in the output.
    assert result.returncode == 0, result.stderr
    assert ("POST", "/api/0/teams/acme/acme/projects/") in [
        call[:2] for call in sentry.calls
    ]
    [created] = [call[2] for call in sentry.calls if call[0] == "POST"]
    assert created == {
        "name": "demo-app",
        "slug": "demo-app",
        "platform": "python-django",
    }
    assert all(
        auth == f"Bearer {TOKEN}" for kind, _, auth in sentry.calls if kind == "GET"
    )
    assert vaulted_dsn(env, project) == DSN
    assert TOKEN not in result.stdout + result.stderr
    assert DSN not in result.stdout + result.stderr


@pytest.mark.parametrize("sentry", [("demo-app",)], indirect=True)
def test_setup_reuses_an_existing_project(tmp_path, sentry):
    # GIVEN a Sentry project that already exists.
    project = project_with_empty_dsn(tmp_path)
    env = sentry_env(tmp_path, sentry.url)

    # WHEN setup runs twice.
    first = setup(env, project)
    again = setup(env, project)

    # THEN nothing is created and the existing project's DSN is vaulted.
    assert first.returncode == 0, first.stderr
    assert again.returncode == 0, again.stderr
    assert not [call for call in sentry.calls if call[0] == "POST"]
    assert vaulted_dsn(env, project) == DSN


@pytest.mark.parametrize("sentry", [()], indirect=True)
def test_rejected_login_changes_nothing_and_names_the_token_page(tmp_path, sentry):
    # GIVEN a login Sentry rejects.
    project = project_with_empty_dsn(tmp_path)
    before = (project / "deployment" / "group_vars" / "all.yml").read_text()
    env = sentry_env(tmp_path, sentry.url)
    sentry.status = 401

    # WHEN setup and check run.
    result = setup(env, project)
    check = startup(env, project, "sentry", "check")

    # THEN both fail naming the token page, the token stays private and
    # neither a project nor the vault was changed.
    for run in (result, check):
        assert run.returncode != 0
        assert "/settings/account/api/auth-tokens/" in run.stderr
        assert TOKEN not in run.stdout + run.stderr
    assert not [call for call in sentry.calls if call[0] == "POST"]
    assert (project / "deployment" / "group_vars" / "all.yml").read_text() == before


def test_missing_login_without_a_terminal_names_the_token_page_and_scopes(tmp_path):
    # GIVEN no ~/.sentryclirc and no SENTRY_AUTH_TOKEN.
    env, _ = keychain_stand_in(tmp_path)
    env["HOME"] = str(tmp_path)
    env.pop("SENTRY_AUTH_TOKEN", None)

    # WHEN the login is checked without a terminal.
    result = startup(env, tmp_path, "sentry", "check", "--org", "acme")

    # THEN it fails fast, naming the personal token page and its scopes,
    # and writes no login.
    assert result.returncode != 0
    assert "https://sentry.io/settings/account/api/auth-tokens/" in result.stderr
    for scope in ("org:read", "team:read", "project:read", "project:write"):
        assert scope in result.stderr
    assert not (tmp_path / ".sentryclirc").exists()


def bootstrap_context(tmp_path, **options):
    from cli.wizard.context import BootstrapContext

    return BootstrapContext(
        "demo", "demo.example", "", "owner", "17", "", tmp_path, **options
    )


def test_sentry_step_precedes_first_push_only_when_requested(tmp_path):
    from cli.wizard.runner import steps_for
    from cli.wizard.steps.finalize import FinalizeStep
    from cli.wizard.steps.sentry import SentryStep

    ctx = bootstrap_context(tmp_path, sentry_org="acme")
    assert steps_for(ctx)[-2:] == [SentryStep, FinalizeStep]
    ctx.sentry_org = None
    assert SentryStep not in steps_for(ctx)


def test_missing_sentry_login_stops_bootstrap_before_resources(monkeypatch, tmp_path):
    import click

    from cli.wizard.runner import run_wizard

    # GIVEN a bootstrap that asks for Sentry on a computer without its login.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("SENTRY_AUTH_TOKEN", raising=False)
    ctx = bootstrap_context(
        tmp_path, sentry_org="acme", without_auth=True, non_interactive=True
    )

    # WHEN it starts, THEN it stops naming the token page before creating
    # the project.
    with pytest.raises(click.ClickException, match="auth-tokens"):
        run_wizard(ctx)
    assert not ctx.project_dir.exists()


def test_conflicting_sentry_choices_are_refused(tmp_path):
    import click

    from cli.wizard.runner import run_wizard

    ctx = bootstrap_context(
        tmp_path, sentry_org="acme", without_sentry=True, without_auth=True
    )
    with pytest.raises(click.ClickException, match="only one of --sentry-org"):
        run_wizard(ctx)
    assert not ctx.project_dir.exists()


def terminal_login(tmp_path, url, token, sentryclirc=None):
    """Run `startup sentry check` on a terminal and paste `token` when asked."""
    home = tmp_path / "home"
    home.mkdir()
    if sentryclirc is not None:
        (home / ".sentryclirc").write_text(sentryclirc)
    browser = tmp_path / "browser"
    browser.write_text(f'#!/bin/sh\necho "$1" >> "{tmp_path}/opened"\n')
    browser.chmod(0o755)
    code, stdout, transcript = cli_in_terminal(
        tmp_path,
        "check",
        group="sentry",
        env={
            "HOME": str(home),
            "BROWSER": str(browser),
            "SENTRY_URL": url,
            "SENTRY_AUTH_TOKEN": None,
            "SENTRY_ORG": None,
        },
        answers=[(b"Paste the Sentry token:", token)],
    )
    output = stdout + transcript
    # The pasted token is never echoed, printed or passed as an argument.
    assert token.encode() not in output
    assert token not in argv_log(tmp_path)
    return code, output.decode(errors="replace"), home / ".sentryclirc"


@pytest.mark.parametrize("sentry", [()], indirect=True)
def test_terminal_login_opens_the_personal_token_page_and_saves_a_valid_token(
    tmp_path, sentry
):
    # GIVEN a terminal without a Sentry login.
    # WHEN `startup sentry check` runs and a personal token is pasted.
    code, output, sentryclirc = terminal_login(tmp_path, sentry.url, TOKEN)

    # THEN the personal token page was opened and named with its scopes, the
    # token was validated, saved privately with the only organization, and
    # used right away.
    assert code == 0, output
    page = f"{sentry.url}/settings/account/api/auth-tokens/"
    assert (tmp_path / "opened").read_text().split() == [page]
    assert page in output
    assert "org:read, team:read, project:read, project:write" in output
    assert "Sentry access to acme verified." in output
    assert ("CONTROL", "/api/0/", None) in sentry.calls
    assert stat.S_IMODE(sentryclirc.stat().st_mode) == 0o600
    assert sentryclirc.read_text().split() == [
        "[auth]",
        "token",
        "=",
        TOKEN,
        "[defaults]",
        "url",
        "=",
        sentry.url,
        "org",
        "=",
        "acme",
    ]


@pytest.mark.parametrize("sentry", [()], indirect=True)
def test_terminal_login_refuses_an_organization_token_and_saves_nothing(
    tmp_path, sentry
):
    # GIVEN a terminal without a Sentry login.
    # WHEN an organization token, which only carries org:ci, is pasted.
    code, output, sentryclirc = terminal_login(tmp_path, sentry.url, ORG_TOKEN)

    # THEN the missing scopes are named and no login is saved.
    assert code != 0
    assert "lacks org:read, team:read, project:read, project:write" in output
    assert "Nothing was saved." in output
    assert not sentryclirc.exists()


@pytest.mark.parametrize("sentry", [()], indirect=True)
def test_terminal_login_replaces_an_unusable_token_and_keeps_other_settings(
    tmp_path, sentry
):
    # GIVEN a ~/.sentryclirc from `sentry-cli login` holding an organization
    # token next to other settings.
    existing = (
        "[defaults]\norg = acme\nproject = Other\n\n"
        f"[auth]\ntoken = {ORG_TOKEN}\n\n[http]\nproxy_url = http://proxy.test\n"
    )

    # WHEN `startup sentry check` runs and a personal token is pasted.
    code, output, sentryclirc = terminal_login(
        tmp_path, sentry.url, TOKEN, sentryclirc=existing
    )

    # THEN only the token changes and the other settings remain.
    assert code == 0, output
    assert "The saved Sentry login is unusable" in output
    saved = sentryclirc.read_text()
    assert f"token = {TOKEN}" in saved
    assert ORG_TOKEN not in saved
    for line in ("org = acme", "project = Other", "proxy_url = http://proxy.test"):
        assert line in saved
    assert stat.S_IMODE(sentryclirc.stat().st_mode) == 0o600
