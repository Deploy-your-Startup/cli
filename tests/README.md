# CLI tests

Run from the CLI repository root. Install global uv and mise; `.python-version`
selects Python and the dev extra supplies pytest, ruff and ty.

```bash
uv sync --locked --extra dev
mise run test
mise run lint
```

For focused runs:

```bash
uv run --extra dev pytest tests/test_onboarding.py tests/test_bootstrap.py
uv run --extra dev pytest tests/test_template_commands.py
uv run --extra dev pytest tests/vault_features
uv run --extra dev pytest tests/vault_features/test_create_and_stdin.py::test_field_stdin_keeps_the_value_out_of_the_arguments -v
```

The suite covers bootstrap and resume behavior, prerequisite checks, GitHub
setup, shared-role sync, Copier adoption/updates, deployment and backup/restore
commands, Hetzner/Cloudflare automation, optional Auth0, and Vault operations.
New tests must exercise real entrypoints and replace only external service
boundaries with local stand-ins. A passing suite does not prove that live cloud
provisioning or browser login works.

Vault tests use temporary fixtures and synthetic credentials. Keep real tokens,
Keychain passwords and decrypted production data out of test inputs and output.
For manual checks, use the `startup` commands documented in the
[CLI README](../README.md#manage-secrets), starting with `--dry-run`. Use the current CLI commands when creating manual checks.

## Vault and template integration runs

```bash
mise run test-vault
```

This run uses the real CLI, Ansible executables, Copier and disposable files.
It covers exact multiline values, whitespace, comments, nested inline fields,
creation, wrong passwords, dry runs, rotation, credential transport and the
built wheel's dependency/license boundary. The CLI process is also tested with
Ansible Python imports blocked; Ansible runs in its own process.

The template matrix renders `django-backend-template` and `vue-django-template`
from immutable public Git revisions pinned in
`test_vault_templates_integration.py`. It checks single-field updates, mixed
batches across files, regeneration of every inline field, and bootstrap's real
batch-update and strict-rotation entrypoints for Hetzner and BYOS. The latter
includes a disposable real SSH key and synthetic provider credentials. It does
not run cloud provisioning or publish repositories.

GitHub access is required to fetch these public template revisions. For an
offline run, set `STARTUP_VAULT_TEMPLATE_ROOT` to a directory containing clones
named `django-backend-template` and `vue-django-template` with the pinned commits
already present. Templates are fetched into temporary test checkouts; source
clones and existing applications are left intact.

## Ubuntu release upgrade integration checks

`uv run --extra dev pytest tests/test_os_upgrade_integration.py` runs the real
CLI, git repositories, Ansible Vault and Ansible playbooks with synthetic
credentials. It checks preview defaults, execution requirements, immutable
role selection and limits that cannot escape the selected environment.
The shared deployment repository separately runs the actual OS upgrade driver
and complete playbook against disposable Ubuntu Docker nodes. Those checks
simulate the release upgrader, reboot and k3s boundaries; they do not qualify
an Ubuntu release for production.

## Guided onboarding integration checks

`uv run --extra dev pytest tests/test_doctor_integration.py` runs the real doctor
command with isolated local prerequisites and a GitHub CLI stand-in. It checks
success/failure symbols, terminal colors, redirected output, login/scope guidance,
nonzero exit status for missing requirements and credential-output redaction.

`uv run --extra dev pytest tests/test_onboarding_integration.py tests/test_vault_guard.py`
exercises the actual CLI prompts, the domain ownership choice, optional settings,
cancellation and real vault files. Cloud accounts and paid infrastructure are not
created by these checks.

`tests/test_launch_animation_integration.py` uses a real pseudo-terminal to check
a single launch, stationary prompts, motion opt-out and cursor restoration on interruption.

## Bootstrap cost integration checks

`uv run --extra dev pytest tests/test_bootstrap_cost_integration.py` exercises
real group_vars (including Vault scalars), decimal estimates, consent, caps,
missing prices and HTTP failures against a local Hetzner API stand-in.
`HCLOUD_API_URL` overrides the Cloud API base URL for isolated provider tests.
The macOS bootstrap process checks use local Git repositories and stand-ins for
GitHub and Keychain; they verify cancellation prevents workflow dispatch.
They do not verify live pricing or provisioning.

## Domain checks

`uv run --extra dev pytest tests/test_domain_check_integration.py` drives real
CLI subprocesses and the domain wizard against local TCP WHOIS and HTTP RDAP
and pricing servers. The pricing fixture is Hetzner’s public EUR feed; browser
launch is replaced only at the Playwright boundary. These checks place no
orders and do not verify authenticated KonsoleH screens.

## KPI bootstrap integration checks

`test_kpis_bootstrap_integration.py` runs real Copier, project bootstrap, Git
pinning and Ansible Vault encryption/rotation/decryption. A disposable owner
repository and external GitHub/Auth0/Keychain stand-ins keep it local. It covers
three independent secrets, a stable UUID and pin, no changes on resume, old
owner copies missing shared roles, and idempotent Auth0 setup for both toggle
names. The template revision is fixed in the test; set
`STARTUP_KPIS_TEMPLATE_ROOT` to a local clone containing that revision.

## Kubernetes SSH access

`uv run --extra dev pytest tests/test_kube_tunnel_e2e.py tests/test_firewall_integration.py`
uses a real Docker SSH server, mutually authenticated TLS and real kubectl clients.
It covers on-demand reconnect, simultaneous clients, separate namespaces, repeated
setup, rejected SSH credentials and TLS identity, and private file permissions.
The firewall integration runs the real CLI, immutable role checkout and Ansible
against a local Hetzner HTTP stand-in, checking preview, API closure, preserved
web/SSH/internal rules and the attached-project owner guard. It requires a sibling
`deploy-template` checkout; Docker or kubectl absence is reported as a skip.
Live cloud firewall attachment, changing networks and sleep/wake remain separate
operational verification; interrupted streaming sessions can require a retry.

Public policy configuration: `uv run --extra dev pytest tests/test_site_policy_integration.py`
drives real CLI validation, renders a Copier project, verifies policy installation
and resume preservation, and rejects incompatible template capabilities before
provider setup. This validates configuration, not legal sufficiency.
