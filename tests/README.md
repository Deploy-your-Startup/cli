# CLI tests

Run from the CLI repository root. Install global uv and mise; `.python-version`
selects Python and the dev extra supplies pytest, ruff and ty.

```bash
uv sync --locked --extra dev
mise run test
mise run lint
```

The full suite uses pytest-xdist with small scheduling batches so long scenarios
do not queue behind other long scenarios on one worker. It prints the 20 slowest
tests. CI runs three independent runners with `./make.sh test --shard 1/3`
(and `2/3`, `3/3`); sorted test IDs are distributed round-robin, including
parametrized cases. The default local command still runs every test. The `test`
check requires every shard to succeed, including on cancellation or skips. Every worker has
its own temporary Ansible home and collection directory: concurrent real Galaxy
installs cannot overwrite each other or modify the developer's collections.
`test_ansible_isolation_integration.py` verifies this with two real pytest workers
and Ansible's effective configuration. CI checks Docker and kubectl before
starting the suite; SSH and browser scenarios remain enabled.

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

`test_one_password_integration.py` covers the 1Password password backend and
`startup config`: a fake `op` on PATH keeps items in a JSON file and records
its arguments, so the tests assert that the password never enters them. It
covers configuration, backend precedence, a missing vault, a locked app,
copying from the Keychain, recovery into 1Password and the bootstrap offer. It
does not verify a real 1Password account or the app's approval prompt.

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

Factory prerequisite regressions exercise real `startup doctor`, `factory init
--setup` and `factory onboard` processes with isolated PATHs: missing tools fail
before workspace creation, doctor never installs tools, and existing owner files
remain intact. The public Factory onboarding E2E still covers real workspace setup
and launch cancellation.

`uv run --extra dev pytest tests/test_doctor_integration.py` runs the real doctor
command with isolated local prerequisites and a GitHub CLI stand-in. It checks
success/failure symbols, terminal colors, redirected output, login/scope guidance,
nonzero exit status for missing requirements and credential-output redaction.
It also checks that basic setup needs no server dependencies and that BYOS can
select a custom public-key path without Chromium or a Hetzner account.

`uv run --extra dev pytest tests/test_onboarding_integration.py tests/test_vault_guard.py`
exercises the actual CLI prompts, the domain ownership choice, optional settings,
cancellation, all three stack choices, name/template/folder/target/account prompt
order, native GitHub re-login and cancellation, environment-token reuse and
account-change guidance, explicit flag compatibility,
BYOS domain guidance and real vault files. `test_ssh_key_selection_integration.py`
validates real OpenSSH keys and rejects private/malformed files without leaking
contents. Cloud accounts and paid infrastructure are not
created by these checks.

`tests/test_launch_animation_integration.py` uses a real pseudo-terminal to check
a single launch, stationary prompts, motion opt-out and cursor restoration on interruption.

`tests/test_prompt_editing_integration.py` sends actual Backspace/Delete bytes,
Ctrl+U and cursor keys through a pseudo-terminal into the real bootstrap process.
It tests both common terminal erase settings and checks that the edited project
name reaches the launch plan without creating resources. This covers local
terminal editing, not installation or live provider setup. On Python builds
without the optional standard-library readline module, basic terminal editing
remains dependent on the terminal's configured erase key.

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


## Factory lifecycle and startup registration

`uv run --extra dev pytest tests/test_factory_integration.py` exercises real CLI
processes, Copier sources and Git repositories. It covers create/resume,
nonempty/link/foreign-source refusal, owner file/comment preservation, duplicate
and malformed registries, credential-bearing remotes, the Factory bootstrap
folder and cancellation. Two complete pitch bootstrap scenarios use the real
wizard, rendering, commits and pushes to a local bare Git repository. Only
GitHub commands and Cloudflare HTTP boundaries are substituted. They cover
success, failure after repository creation, automatic registration and resume
without overwriting application edits.

The Factory template's own lifecycle suite checks neutral exports, generated
setup scripts, Sprowi and owner-preserving Copier upgrades. Homebrew downloads,
fresh macOS binary installation, live GitHub OAuth, engine credentials and
cloud provisioning remain separate qualification; none are proven by these
local scenarios.


The required macOS `factory-onboarding` CI job runs
`uv run --extra dev python scripts/check_factory_onboarding_e2e.py`. It fetches
the real public Factory release into an isolated home, installs mise runtimes,
clones all public tooling, installs agent skills, reviews the startup plan and
cancels before provider setup. Only GitHub identity is substituted. The required
`test` aggregate fails if this E2E job fails or is skipped. This complements the
bootstrap persistence tests; it does not provision a live application.

### BYOS finalization

`test_byos_finalize_integration.py` exercises server selection, finalization and
summary in real processes with real Git repositories and an SSH server in Docker.
It verifies deploy-key installation and reuse, authentication with that key,
workflow dispatch, and denied SSH stopping before push with repair instructions.
GitHub and Keychain use local boundary stand-ins. Terminal summary coverage also
checks that BYOS no longer requests manual setup or deployment.

These checks do not run a fresh public GitHub Actions deployment or install k3s
on a real BYOS VPS; those provider flows remain unverified by this local suite.

## Cloudflare browser setup

`uv run --extra dev pytest tests/test_cloudflare_browser_integration.py` launches
managed Chromium with disposable persistent profiles against a local HTTP
provider. It checks ready, delayed and denied sessions, no repeated navigation
while the token page loads, and Stealth scripts in the first tab, new tabs and
iframes. The public token-creation entrypoint also fills every permission row,
submits the form once and extracts a delayed one-time token from either the
success box or curl example without printing it. Both template-picker and direct
form entry are covered. Managed Chromium must be installed. Live Cloudflare login,
challenges, permission selectors and API token creation require separate account
verification; local provider checks cannot establish live compatibility.


Factory progress recovery scenarios in `tests/test_factory_integration.py` run
real CLI processes with isolated state directories. They cover failure before
folder creation, setup failure and resume from saved options, interrupted snapshot
publication, owner-file conflicts, differing resume options, actual-file status,
credential URL rejection and missing state. External tool failures use a local
mise boundary; owner files and persisted JSON are real. The macOS Factory E2E
also checks status and resumes setup with real workspace tools.

## Managed Chromium prerequisites and sessions

SSH container fixtures send their build contexts as tar archives on stdin. This
keeps Docker and remote Podman builds independent of VM host-directory sharing,
including pytest temporary directories on macOS. Build failures include the
container engine's output rather than only a subprocess exit code.

Install the full browser before running the suite:
`uv run --extra dev python -m playwright install chromium` (CI adds `--with-deps`).
This includes the headed executable checked by doctor and the headless shell used
by fixture scenarios. Browser downloads remain explicit. Use the same
`PLAYWRIGHT_BROWSERS_PATH` for installation and test execution if overriding it.

`test_doctor_integration.py` runs the real CLI against isolated browser caches
with a matching executable, a missing revision and a runtime without Playwright.
Basic and BYOS checks remain browser-independent. Doctor never launches or
installs a browser. The Hetzner, KonsoleH and Cloudflare browser scenarios use
managed Chromium against local provider fixtures, including persistent cookies
across browser restarts and the shared Hetzner/KonsoleH profile.
`test_auth0_browser_integration.py` verifies the public login entrypoint against
a local TLS provider twice, checking managed launch arguments and fresh ephemeral
sessions; only headed display and trust of the disposable certificate differ.
Live account login, challenges and provider operations remain separate manual
checks. A headed smoke check can use a disposable profile and local HTML without
accessing any provider account.

Chrome selection scenarios set `STARTUP_BROWSER=chrome` and use the existing
`chrome-profile` name separately from managed Chromium. The Hetzner/KonsoleH,
Cloudflare and Auth0 session tests also run with installed system Chrome;
these optional cases explicitly skip when Chrome is absent. Hetzner checks the
actual browser arguments to ensure extension-disabling defaults are removed.
Doctor tests reject invalid selections and check Chrome without a Chromium cache.
The terminal-output test drains the PTY concurrently, avoiding macOS buffer
backpressure while still checking real ANSI colors and secret redaction.

Manual release checks remain required: on macOS, enable Apple Passwords in the
dedicated Chrome profile, unlock it and complete headed Hetzner Cloud/KonsoleH,
Cloudflare login/challenges and Auth0 verification. Record the browser/macOS
versions and outcomes without credentials. Auth0 profiles are ephemeral and need
extension setup each time. Headless fixture passes do not establish these live
provider or Apple Passwords checks.
