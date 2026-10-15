"""Real Copier/bootstrap/Vault processes; only GitHub, Auth0 and Keychain stand in."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from uuid import UUID

import pytest
import yaml

from cli.bootstrap import TEMPLATE_VAULT_PASSWORD
from tests.vault_support import RealVault

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE_REVISION = "67ec8ef27d4209f1c0106ed27f3547d39a95625c"
TEMPLATE_SOURCE = "https://github.com/Deploy-your-Startup/vue-django-template.git"


def process(directory, env, code, payload):
    return subprocess.run(
        [sys.executable, "-c", code],
        input=json.dumps(payload),
        text=True,
        cwd=directory,
        env=env,
        capture_output=True,
        check=False,
        timeout=180,
    )


@pytest.fixture
def bootstrap_environment(tmp_path):
    # GIVEN a committed real template and a real local shared deployment repo.
    template = tmp_path / "template"
    source = os.environ.get("STARTUP_KPIS_TEMPLATE_ROOT", TEMPLATE_SOURCE)
    if os.environ.get("STARTUP_KPIS_TEMPLATE_ROOT"):
        # Include unpublished template fixes in a disposable committed fixture.
        shutil.copytree(
            source,
            template,
            ignore=shutil.ignore_patterns(
                ".git", ".venv", "node_modules", "__pycache__", ".pytest_cache"
            ),
        )
        subprocess.run(["git", "-C", str(template), "init", "-q"], check=True)
        subprocess.run(["git", "-C", str(template), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(template),
                "-c",
                "user.name=Integration",
                "-c",
                "user.email=test@example.com",
                "commit",
                "-qm",
                "template fixture",
            ],
            check=True,
        )
    else:
        subprocess.run(["git", "clone", "--quiet", source, str(template)], check=True)
        subprocess.run(
            ["git", "-C", str(template), "checkout", "--quiet", TEMPLATE_REVISION],
            check=True,
        )
    shared = tmp_path / "shared"
    for role in ("oauth2-proxy", "kpis"):
        path = shared / "roles" / role / "tasks"
        path.mkdir(parents=True)
        (path / "main.yml").write_text(
            "- ansible.builtin.debug:\n    msg: integration role\n"
        )
    for command in (
        ["init", "-b", "main"],
        ["add", "."],
        [
            "-c",
            "user.name=Integration",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-qm",
            "shared roles",
        ],
    ):
        subprocess.run(
            ["git", "-C", str(shared), *command], check=True, capture_output=True
        )
    key = tmp_path / "id_ed25519"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True
    )
    # OS public key lookup is an external boundary; give it a disposable home.
    home = tmp_path / "home"
    (home / ".ssh").mkdir(parents=True)
    shutil.copyfile(str(key) + ".pub", home / ".ssh/id_ed25519.pub")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    keychain = tmp_path / "keychain"
    calls = tmp_path / "gh-calls.jsonl"
    gh = binaries / "gh"
    gh.write_text(f"""#!{sys.executable}
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['TEST_GH_CALLS'], 'a') as output: output.write(json.dumps(args)+'\\n')
if args[:2] == ['auth', 'status']: print("Token scopes: 'repo', 'read:packages', 'write:packages'")
elif args[:2] == ['auth', 'token']: print('synthetic-github-token')
elif args[:2] == ['repo', 'view']: pass
elif args and args[0] == 'api': sys.exit(1 if os.environ.get('TEST_MISSING_ROLES') else 0)
else: sys.exit(1)
""")
    security = binaries / "security"
    security.write_text(f"""#!{sys.executable}
import os, sys
from pathlib import Path
path = Path(os.environ['TEST_KEYCHAIN'])
if sys.argv[1] == 'find-generic-password' and path.exists(): print(path.read_text())
else: sys.exit(1)
""")
    startup = binaries / "startup"
    startup.write_text(f'#!/bin/sh\nexec "{sys.executable}" -m cli.startup "$@"\n')
    auth0 = binaries / "auth0"
    auth0.write_text(f"""#!{sys.executable}
import json, sys
args=sys.argv[1:]
if args[:2] == ['apps','list']: print(json.dumps([{{'name':'kpi-demo','client_id':'synthetic-client','callbacks':['https://demo.example/oauth2/callback']}}]))
elif args[:2] == ['apps','show']: print(json.dumps({{'client_id':'synthetic-client','client_secret':'synthetic-auth0-client-secret'}}))
elif args[:2] == ['apis','list']: print(json.dumps([{{'identifier':'https://demo.example/private_api'}}]))
else: sys.exit(1)
""")
    for binary in binaries.iterdir():
        binary.chmod(0o755)
    env = {
        **os.environ,
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "PYTHONPATH": str(ROOT / "src"),
        "HOME": str(home),
        "TEST_KEYCHAIN": str(keychain),
        "TEST_GH_CALLS": str(calls),
        "STARTUP_ANSIBLE_REPO_URL": shared.as_uri(),
        "GIT_AUTHOR_NAME": "Integration",
        "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "Integration",
        "GIT_COMMITTER_EMAIL": "test@example.com",
    }
    return tmp_path, env, template, shared, keychain, calls


BOOTSTRAP = r"""
import json, os, sys, types
from pathlib import Path
from cli.wizard.context import BootstrapContext
from cli.wizard.steps.project import ProjectStep
# Keychain is the sole unavailable OS service. All bootstrap and Vault code is real.
class Keyring:
    def set_password(self, service, user, value):
        Path(os.environ['TEST_KEYCHAIN']).write_text(value)
module = types.ModuleType('keyring.backends.macOS')
module.Keyring = Keyring
sys.modules['keyring.backends.macOS'] = module
payload = json.load(sys.stdin)
ctx = BootstrapContext('kpi-demo', 'demo.example', '', 'example', '18.6', '',
                       Path(payload['output']), provider='byos', byos_host='192.0.2.1',
                       template_source=payload['template'], template_version='HEAD',
                       non_interactive=True, without_auth=True)
step = ProjectStep()
if payload.get('resume'):
    assert step.check(ctx)
else:
    sys.platform = 'darwin'
    step.run(ctx)
"""


def bootstrapped(environment):
    directory, env, template, _shared, _keychain, _calls = environment
    result = process(
        directory,
        env,
        BOOTSTRAP,
        {"output": str(directory / "projects"), "template": str(template)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return directory / "projects/kpi-demo", result


def test_bootstrap_generates_secrets_uuid_pin_and_preserves_them_on_resume(
    bootstrap_environment,
):
    directory, env, template, shared, keychain, _calls = bootstrap_environment
    # WHEN the real project bootstrap step renders and encrypts the project.
    project, result = bootstrapped(bootstrap_environment)
    deployment = project / "deployment"
    values = yaml.load(
        (deployment / "group_vars/all.yml").read_text(), Loader=yaml.BaseLoader
    )
    website = UUID(values["kpis_website_id"])
    template_settings = yaml.load(
        (template / "template/deployment/group_vars/all.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    assert website.version == 4
    assert values["kpis_enabled"] == template_settings["kpis_enabled"]
    password = keychain.read_text()
    vault = RealVault(password)
    production = yaml.load(
        (deployment / "group_vars/production.yml").read_text(), Loader=yaml.BaseLoader
    )
    secrets = [
        vault.decrypt(production[name])
        for name in ("kpis_db_password", "kpis_app_secret", "kpis_admin_password")
    ]
    seed_values = yaml.load(
        (template / "template/deployment/group_vars/production.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    seed_vault = RealVault(TEMPLATE_VAULT_PASSWORD)
    public_seeds = [
        seed_vault.decrypt(seed_values[name])
        for name in ("kpis_db_password", "kpis_app_secret", "kpis_admin_password")
    ]
    # THEN values themselves change, rather than only re-encrypting public seeds.
    assert all(secret not in public_seeds for secret in secrets)
    with pytest.raises(subprocess.CalledProcessError):
        seed_vault.decrypt(production["kpis_admin_password"])
    # THEN all three credentials are random, encrypted and absent from output/answers.
    assert len(set(secrets)) == 3 and all(len(secret) == 32 for secret in secrets)
    visible = (
        result.stdout + result.stderr + (project / ".copier-answers.yml").read_text()
    )
    assert password not in visible
    assert all(secret.decode() not in visible for secret in secrets)
    revision = subprocess.check_output(
        ["git", "-C", str(shared), "rev-parse", "HEAD"], text=True
    ).strip()
    assert (deployment / "shared-roles.ref").read_text().strip() == revision
    assert len((deployment / "shared-roles.sha256").read_text().strip()) == 64
    before = {
        path: path.read_bytes() for path in deployment.rglob("*") if path.is_file()
    }
    # WHEN bootstrap resumes after configuration.
    resumed = process(
        directory,
        env,
        BOOTSTRAP,
        {
            "output": str(directory / "projects"),
            "template": str(template),
            "resume": True,
        },
    )
    # THEN neither the website identity nor existing Vault ciphertext/pin changes.
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert all(path.read_bytes() == content for path, content in before.items())


@pytest.mark.parametrize("missing", [False, True])
def test_existing_shared_repo_requires_the_new_roles_before_bootstrap(
    bootstrap_environment, missing
):
    directory, env, template, _shared, _keychain, calls = bootstrap_environment
    # GIVEN an existing shared repository with or without the requested roles.
    if missing:
        env = {**env, "TEST_MISSING_ROLES": "1"}
    # WHEN bootstrap decides whether the shared deployment step can be skipped.
    result = process(
        directory,
        env,
        """
import json
from pathlib import Path
from cli.wizard.context import BootstrapContext
from cli.wizard.steps.shared_deployment import SharedDeploymentStep
p=json.load(__import__('sys').stdin)
ctx=BootstrapContext('demo','demo.example','','example','18.6','',Path('.'),template_source=p['template'])
print(SharedDeploymentStep().check(ctx))
""",
        {"template": str(template)},
    )
    # THEN old owner copies request sync while compatible owner copies are retained.
    assert result.returncode == 0, result.stderr
    assert result.stdout.rstrip().endswith(str(not missing))
    observed = [json.loads(line) for line in calls.read_text().splitlines()]
    assert observed[0][:2] == ["repo", "view"]
    assert any(
        "roles/oauth2-proxy/tasks/main.yml" in " ".join(command) for command in observed
    )
    assert not (directory / "projects").exists()


@pytest.mark.parametrize("legacy", [False, True])
def test_auth0_setup_uses_shared_role_and_preserves_cookie_on_repeat(
    bootstrap_environment, legacy
):
    directory, env, _template, _shared, keychain, _calls = bootstrap_environment
    project, _ = bootstrapped(bootstrap_environment)
    if legacy:
        (project / "oauth2-proxy/deployment").mkdir(parents=True)
        settings = project / "deployment/group_vars/all.yml"
        settings.write_text(
            settings.read_text().replace("oauth2_proxy_enabled:", "oauth_enabled:")
        )
    command = [
        sys.executable,
        "-m",
        "cli.startup",
        "auth0",
        "setup",
        "--tenant",
        "tenant.eu.auth0.com",
        "--project-name",
        "kpi-demo",
        "--base-domain",
        "demo.example",
        "--project-dir",
        str(project),
    ]
    # WHEN real Auth0 setup receives provider credentials and writes actual Vault fields.
    result = subprocess.run(
        command,
        cwd=directory,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    target = project / "deployment/group_vars/production.yml"
    first = target.read_bytes()
    values = yaml.load(first, Loader=yaml.BaseLoader)
    vault = RealVault(keychain.read_text())
    toggle = "oauth_enabled" if legacy else "oauth2_proxy_enabled"
    assert vault.decrypt(values[toggle]) == b"true"
    assert (
        vault.decrypt(values["oauth2_proxy_client_secret"])
        == b"synthetic-auth0-client-secret"
    )
    # THEN no credentials reach diagnostic output and a second setup preserves the key.
    assert "synthetic-auth0-client-secret" not in result.stdout + result.stderr
    repeated = subprocess.run(
        command,
        cwd=directory,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=120,
    )
    assert repeated.returncode == 0, repeated.stdout + repeated.stderr
    assert target.read_bytes() == first
