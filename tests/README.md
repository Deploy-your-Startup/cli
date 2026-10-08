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

`uv run --extra dev pytest tests/test_onboarding_integration.py tests/test_vault_guard.py`
exercises the actual CLI prompts, the domain ownership choice, optional settings,
cancellation and real vault files. Cloud accounts and paid infrastructure are not
created by these checks.

`tests/test_launch_animation_integration.py` uses a real pseudo-terminal to check
a single launch, stationary prompts, motion opt-out and cursor restoration on interruption.
