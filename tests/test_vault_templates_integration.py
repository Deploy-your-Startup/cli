"""Real template renders, batch secret updates and bootstrap's Vault lifecycle.

Template revisions are public immutable commits. Tests fetch only these source
repositories; no cloud accounts, deployed applications or real credentials are
used. STARTUP_VAULT_TEMPLATE_ROOT can point at local clones containing the pins.
"""

import base64
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from cli.bootstrap import TEMPLATE_VAULT_PASSWORD
from tests.test_vault_process_integration import LAUNCH, cli
from tests.vault_support import RealVault

SOURCE = Path(__file__).resolve().parents[1] / "src"
TEMPLATES = {
    "django-backend-template": "1da17451060b5617613815a8a1d62c5df74cb43f",
    "vue-django-template": "6f5543eb937530ae6509225e6007e98a24ece58c",
}
NEW_PASSWORD = "template-integration-new-password"
SENTRY = "https://synthetic@example.invalid/42"
TOKEN = "synthetic-hetzner-token-only"
DOCKER = base64.b64encode(
    json.dumps(
        {"auths": {"ghcr.io": {"auth": "ZXhhbXBsZS1vd25lcjpzeW50aGV0aWM="}}}
    ).encode()
).decode()


class VaultScalar(str):
    pass


class TemplateLoader(yaml.SafeLoader):
    pass


TemplateLoader.add_constructor(
    "!vault", lambda loader, node: VaultScalar(loader.construct_scalar(node))
)


def state(deployment):
    return {
        path.name: yaml.load(path.read_text(), Loader=TemplateLoader)
        for path in (deployment / "group_vars").glob("*.yml")
    }


def scalars(snapshot):
    return {
        name: value
        for data in snapshot.values()
        for name, value in data.items()
        if isinstance(value, VaultScalar)
    }


def public_values(snapshot):
    return {
        filename: {
            name: value
            for name, value in data.items()
            if not isinstance(value, VaultScalar)
        }
        for filename, data in snapshot.items()
    }


def child(tmp_path, code, payload):
    # Use the same import guard and real-process audit as the CLI integration tests.
    prefix = LAUNCH[: LAUNCH.index("runpy.run_module")]
    return subprocess.run(
        [sys.executable, "-c", prefix + code],
        input=json.dumps(payload).encode(),
        cwd=tmp_path,
        capture_output=True,
        check=False,
        timeout=180,
        env={
            **os.environ,
            "PYTHONPATH": str(SOURCE),
            "VAULT_TEST_ARGV": str(tmp_path / "argv.jsonl"),
        },
    )


@pytest.fixture(scope="session", params=TEMPLATES, ids=list(TEMPLATES))
def template_source(request, tmp_path_factory):
    name = request.param
    directory = tmp_path_factory.mktemp(name)
    local_root = os.environ.get("STARTUP_VAULT_TEMPLATE_ROOT")
    source = (
        str(Path(local_root) / name)
        if local_root
        else f"https://github.com/Deploy-your-Startup/{name}.git"
    )
    for args in (
        ["init", str(directory)],
        ["-C", str(directory), "fetch", "--depth=1", source, TEMPLATES[name]],
        ["-C", str(directory), "checkout", "--detach", "FETCH_HEAD"],
    ):
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=90, check=False
        )
        assert result.returncode == 0, result.stderr
    return directory, TEMPLATES[name]


@pytest.fixture
def rendered(tmp_path, template_source):
    # GIVEN a real public template rendered through the same entrypoint as bootstrap.
    source, revision = template_source
    project = tmp_path / "example"
    key = tmp_path / "ci-key"
    generated = subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-f", str(key)],
        capture_output=True,
        check=False,
        timeout=30,
    )
    assert generated.returncode == 0, generated.stderr
    result = child(
        tmp_path,
        """
from pathlib import Path
from cli.template_commands import render_project
payload = json.load(sys.stdin)
render_project(Path(payload['project']), {
    'project_name': 'example', 'base_domain': 'example.invalid',
    'github_username': 'example-owner', 'ci_key': payload['public_key'],
    'user_key': payload['public_key'],
}, source=payload['source'], version=payload['revision'])
""",
        {
            "project": str(project),
            "source": str(source),
            "revision": revision,
            "public_key": key.with_suffix(".pub").read_text().strip(),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return project


def test_single_multiple_and_all_secrets_on_rendered_templates(tmp_path, rendered):
    deployment = rendered / "deployment"
    original = state(deployment)
    originals = scalars(original)
    assert {
        "backend_db_password",
        "postgres_admin_password",
        "backend_secret_key",
        "docker_config_json_b64",
        "backend_sentry_dsn",
    } <= originals.keys()
    # WHEN one secret is replaced, every other encrypted scalar remains byte-identical.
    result = cli(
        tmp_path,
        "update",
        "-r",
        deployment,
        "--field-stdin",
        "backend_db_password",
        "--verify-password",
        stdin=b"  single\nmultiline-value  \n",
        password=TEMPLATE_VAULT_PASSWORD,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    single = scalars(state(deployment))
    vault = RealVault(TEMPLATE_VAULT_PASSWORD)
    assert (
        vault.decrypt(single["backend_db_password"]) == b"  single\nmultiline-value  "
    )
    assert all(
        single[key] == value
        for key, value in originals.items()
        if key != "backend_db_password"
    )
    # WHEN a mixed batch spans all.yml and production.yml.
    result = cli(
        tmp_path,
        "update",
        "-r",
        deployment,
        "--field-stdin",
        "backend_sentry_dsn",
        "--field-random",
        "postgres_admin_password",
        "--field-random",
        "k3s_token",
        "--verify-password",
        stdin=SENTRY.encode(),
        password=TEMPLATE_VAULT_PASSWORD,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    multiple = scalars(state(deployment))
    assert vault.decrypt(multiple["backend_sentry_dsn"]) == SENTRY.encode()
    for field in ("postgres_admin_password", "k3s_token"):
        assert len(vault.decrypt(multiple[field])) == 32
    assert all(
        multiple[key] == value
        for key, value in single.items()
        if key not in {"backend_sentry_dsn", "postgres_admin_password", "k3s_token"}
    )
    # WHEN every inline secret is regenerated, first as a preview, then for real.
    args = ["update", "-r", deployment, "--verify-password"]
    for name in originals:
        args += ["--field-random", name]
    before = {p: p.read_bytes() for p in (deployment / "group_vars").glob("*.yml")}
    preview = cli(tmp_path, *args, "--dry-run", password=TEMPLATE_VAULT_PASSWORD)
    assert preview.returncode == 0, preview.stdout + preview.stderr
    assert all(p.read_bytes() == value for p, value in before.items())
    preview_state = scalars(state(tmp_path / "dry-run-output"))
    assert preview_state.keys() == originals.keys()
    assert all(len(vault.decrypt(value)) == 32 for value in preview_state.values())
    result = cli(tmp_path, *args, password=TEMPLATE_VAULT_PASSWORD)
    assert result.returncode == 0, result.stdout + result.stderr
    final = state(deployment)
    for name, value in scalars(final).items():
        assert value != multiple[name]
        assert len(vault.decrypt(value)) == 32
    # THEN public configuration and all comments remain intact.
    assert public_values(final) == public_values(original)
    for path, value in before.items():
        comments = [
            line for line in value.splitlines() if line.lstrip().startswith(b"#")
        ]
        assert [
            line
            for line in path.read_bytes().splitlines()
            if line.lstrip().startswith(b"#")
        ] == comments


@pytest.mark.parametrize("provider", ["hetzner", "byos"])
def test_bootstrap_secret_batch_and_rotation_on_rendered_templates(
    tmp_path, rendered, provider
):
    deployment = rendered / "deployment"
    original = state(deployment)
    # GIVEN a real disposable SSH key and synthetic provider credentials.
    key = tmp_path / "ci-key"
    private_key = key.read_text()
    # WHEN bootstrap's public batch updater and strict rotation run unmodified.
    result = child(
        tmp_path,
        """
from cli.bootstrap import TEMPLATE_VAULT_PASSWORD
from cli.update_vault_secrets import update_secrets
from cli.rotate_vault import rotate_vault_password
from cli.wizard.vault_guard import verify_rotation
from pathlib import Path
payload = json.load(sys.stdin)
deployment = Path(payload['deployment'])
random_fields = ['k3s_token', 'backend_db_password', 'postgres_admin_password']
if any('backend_secret_key:' in p.read_text() for p in (deployment / 'group_vars').glob('*.yml')):
    random_fields.append('backend_secret_key')
files = [('ci_ssh_key', payload['private_key'])]
if payload['provider'] == 'hetzner':
    files.append(('hcloud_token_production', payload['token']))
success, changed, failed = update_secrets(
    repo=str(deployment), vault_password=TEMPLATE_VAULT_PASSWORD,
    vault_fields=random_fields,
    set_field=[('postgres_admin_username', 'admin'),
               ('docker_config_json_b64', payload['docker']),
               ('backend_sentry_dsn', payload['sentry'])],
    set_file_content=files,
)
assert success and not failed and changed
before = {p: p.read_bytes() for p in deployment.rglob('*') if p.is_file()}
assert rotate_vault_password(repo=str(deployment), old_password=TEMPLATE_VAULT_PASSWORD,
                             new_password=payload['new_password'], strict=True, dry_run=True)
assert all(p.read_bytes() == value for p, value in before.items())
assert rotate_vault_password(repo=str(deployment), old_password=TEMPLATE_VAULT_PASSWORD,
                             new_password=payload['new_password'], strict=True)
verify_rotation(deployment, payload['new_password'], TEMPLATE_VAULT_PASSWORD)
""",
        {
            "deployment": str(deployment),
            "provider": provider,
            "private_key": private_key,
            "token": TOKEN,
            "docker": DOCKER,
            "sentry": SENTRY,
            "new_password": NEW_PASSWORD,
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # THEN every scalar opens under the new key and none under the public template key.
    vault = RealVault(NEW_PASSWORD)
    final = state(deployment)
    expected = {
        "postgres_admin_username": "admin",
        "docker_config_json_b64": DOCKER,
        "backend_sentry_dsn": SENTRY,
    }
    for name, value in scalars(final).items():
        plaintext = vault.decrypt(value)
        if name in expected:
            assert plaintext == expected[name].encode()
        elif name in {
            "k3s_token",
            "backend_db_password",
            "postgres_admin_password",
            "backend_secret_key",
        }:
            assert len(plaintext) == 32
            assert plaintext != RealVault(TEMPLATE_VAULT_PASSWORD).decrypt(
                scalars(original)[name]
            )
        else:
            assert plaintext == RealVault(TEMPLATE_VAULT_PASSWORD).decrypt(
                scalars(original)[name]
            )
        with pytest.raises(subprocess.CalledProcessError):
            RealVault(TEMPLATE_VAULT_PASSWORD).decrypt(value)
    assert (
        vault.decrypt((deployment / "ci_ssh_key").read_bytes()) == private_key.encode()
    )
    full_files = [deployment / "ci_ssh_key"]
    if provider == "hetzner":
        token = deployment / "hcloud_token_production"
        assert vault.decrypt(token.read_bytes()) == TOKEN.encode()
        full_files.append(token)
    else:
        assert not (deployment / "hcloud_token_production").exists()
    for path in full_files:
        with pytest.raises(subprocess.CalledProcessError):
            RealVault(TEMPLATE_VAULT_PASSWORD).decrypt(path.read_bytes())
    assert public_values(final) == public_values(original)
    # Private material must not appear in Copier answers, logs or any process argv.
    visible = (
        result.stdout.decode()
        + result.stderr.decode()
        + (tmp_path / "argv.jsonl").read_text()
        + (rendered / ".copier-answers.yml").read_text()
    )
    for secret in (private_key, TOKEN, DOCKER, SENTRY, NEW_PASSWORD):
        assert secret not in visible

    # AND an actual playbook can load both generated group_vars files and use
    # their decrypted values, as the deployment consumer does.
    marker = tmp_path / "ansible-verified"
    playbook = tmp_path / "verify.yml"
    playbook.write_text(
        json.dumps(
            [
                {
                    "hosts": "localhost",
                    "connection": "local",
                    "gather_facts": False,
                    "vars_files": [
                        str(deployment / "group_vars" / "all.yml"),
                        str(deployment / "group_vars" / "production.yml"),
                    ],
                    "vars": {
                        "ansible_python_interpreter": sys.executable,
                        "expected_docker": DOCKER,
                        "expected_sentry": SENTRY,
                    },
                    "tasks": [
                        {
                            "name": "Verify bootstrapped variables",
                            "no_log": True,
                            "ansible.builtin.assert": {
                                "that": [
                                    "postgres_admin_username | string == 'admin'",
                                    "docker_config_json_b64 | string == expected_docker",
                                    "backend_sentry_dsn | string == expected_sentry",
                                    "backend_secret_key | string | length == 32",
                                    "k3s_token | string | length == 32",
                                    "backend_db_password | string | length == 32",
                                    "postgres_admin_password | string | length == 32",
                                ]
                            },
                        },
                        {
                            "name": "Record verification",
                            "ansible.builtin.copy": {
                                "content": "verified",
                                "dest": str(marker),
                                "mode": "0600",
                            },
                        },
                    ],
                }
            ]
        )
    )
    password_file = tmp_path / "synthetic-playbook-password"
    password_file.write_text(NEW_PASSWORD)
    password_file.chmod(0o600)
    try:
        consumed = subprocess.run(
            [
                str(Path(sys.executable).parent / "ansible-playbook"),
                "-i",
                "localhost,",
                "--vault-password-file",
                str(password_file),
                str(playbook),
            ],
            cwd=tmp_path,
            capture_output=True,
            check=False,
            timeout=60,
        )
        assert consumed.returncode == 0, consumed.stdout + consumed.stderr
        assert marker.read_text() == "verified"
    finally:
        password_file.unlink()
