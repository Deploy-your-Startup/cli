# Startup CLI contributor instructions

## Tests: integration tests only, never unit tests

**Every change ships with integration tests. Do not write unit tests.** This
rule has no exceptions and applies to agents and humans alike.

- An integration test drives the real thing: the real CLI process or public
  function, real git repositories, real files, real scripts. It asserts on
  outcomes a user would see (files, commits, tags, exit codes, output).
- Do not test private helpers in isolation, and do not mock our own code.
  Replace only the boundary we cannot run locally (GitHub API, cloud providers,
  browsers) with a local stand-in such as a bare git repository or a fake `gh`.
- Write tests as GIVEN / WHEN / THEN. A bug fix starts with an integration test
  that reproduces the bug.
- If you notice an existing unit test while working nearby, replace it with an
  integration test that covers the same behavior.
- Here: pytest under `tests/`, run with `mise run test`. Run the real CLI as a
  subprocess (`python -m cli.startup ...`) or call a public entrypoint. For
  GitHub, put a fake `gh` on PATH backed by bare git repositories; see
  `tests/test_sync_versioning_integration.py` for the pattern.

Again, because it matters: **integration tests, never unit tests.**

### Ansible Vault compatibility

Changes to Vault handling, including replacing Ansible Python imports with
`ansible-vault` subprocesses, must preserve the existing inline-field behavior.
Before refactoring, read the current commands and tests and capture any additional
user requirements as integration scenarios. Do not assume a subprocess migration
preserves behavior without exercising it.

- Drive the real `startup secrets` CLI against temporary YAML files and the real
  installed `ansible-vault` executable. Do not mock encryption, decryption, rekeying,
  our Vault code or subprocess execution. Use synthetic test secrets only.
- Cover targeted inline replacement, adjacent and nested fields, varying input
  indentation and the established two-space indentation of rewritten blocks.
  Assert that unrelated fields, comments and surrounding content survive.
- Cover multiline secrets, Unicode, leading/trailing whitespace and final
  newlines. Verify the exact decrypted value with real Ansible; do not use
  `.strip()` in assertions to hide changes to secret contents.
- Cover multiple fields and files, missing-field errors, explicit `--create-in`,
  full-file Vault operations and password rotation for both inline and full-file
  encryption. Verify that rotated data opens with the new password and fails
  with the old one.
- Cover stdin input, password-provider integration, dry runs and wrong-password
  failures. An unavailable OS Keychain may use a local boundary stand-in, but
  Ansible must remain real. Assert dry runs leave files unchanged and failures
  do not corrupt them. Check that secrets never enter child-process arguments
  or diagnostic output; only an explicitly requested secret-read result may
  contain plaintext on stdout.
- Run the template integration matrix for Vault changes: render the real
  Django/FastAPI and Vue + Django/FastAPI templates at pinned public revisions,
  then exercise single-field, mixed multi-file and all-field updates, dry runs,
  and bootstrap's batch secret setup plus strict rotation for Hetzner and BYOS.
  Use disposable rendered projects and synthetic credentials; never provision
  cloud infrastructure for these tests. `mise run test-vault` runs this matrix
  together with the other Vault regression tests.
- Treat these as required checks, not claims that all cases already work. Record
  uncovered behavior or failures explicitly before claiming compatibility.

## Product direction

Use [Deploy Your Startup](https://deploy-your-startup.com) and its
[quickstart](https://deploy-your-startup.com/get-started) as the product reference.
Help developers and coding agents ship a startup in their own accounts and
repeat the workflow for the next idea. Preserve user ownership of code,
infrastructure and deployment configuration. Keep the shared workflow reusable
across application stacks and provide predictable commands and automated checks.

Harness Engineering for startups prioritizes Speed, Control and Scalability.
Use explicit agent instructions, reusable tools and automated checks to build
startups quickly, preserve owner control and support growth.

The public first-deploy path currently targets macOS, Hetzner, Django/FastAPI,
Postgres and HTTPS; Vue is an explicit template option. Verify support before
claiming another stack or provider. Users pay providers directly; the current
no-platform-fee statement applies to early access.

## Public documentation

Write concise, professional English for someone cloning this repository alone.
Use neutral project names and domains. Keep setup self-contained; omit personal
portfolio history, local machine paths and dependencies on the portfolio hub.
Explain prerequisites, the next action and how the user verifies the outcome.
Check commands against actual CLI options and workflow inputs, Markdown fences,
relative links and public release refs. Pin published versions in quickstarts.
Check live installer content before recommending it; an HTTP 200 with HTML is
not a shell installer. Keep the README consistent with the published quickstart
and document advanced or maintainer procedures separately.

Use `startup` for deployment, Ansible and Vault operations. Secret examples use
Keychain and stdin with an initial `--dry-run`; never put credentials in command
arguments, logs or committed files. Require authorization for destructive
operations. Keep `startup sync` (shared workflows/roles) distinct from
`startup template update` (application files). Verify Actions and HTTPS before
reporting a deployment as successful.

## Repository workflow

- Read `README.md`, `pyproject.toml`, `mise.toml` and the relevant command/tests
  before changing behavior. Public command entrypoints live in `src/cli/startup.py`;
  command implementations live in the corresponding modules.
- Use global uv and the committed `.python-version`. Run tasks through
  `mise -C <checkout> run <task>` or `mise -C <checkout> exec -- ...`.
- `mise run format` applies formatting/fixes. `mise run lint` only checks
  formatting, lint and types; `mise run test` runs pytest. Use pinned project
  tools via uv, with the dev extra for focused tests. Do not add git hooks.
- Keep user commands independent of the contributor checkout. CLI installation
  must provide its runtime dependencies; local application development has its
  own prerequisites documented by the generated project.
- Treat interrupted bootstrap runs as resumable. Preserve existing repositories,
  user customizations and encrypted project configuration.
- Browser setup and account-dependent operations need separate live verification;
  local integration tests do not establish a successful first deployment.
- `startup sync` syncs releases of the shared deploy template (default: newest
  `vX.Y.Z`), tags the user's repository with the exact tag and moves the `vX`
  major tag. Generated workflows reference `@v1` (`DEPLOY_MAJOR_REF`); local
  `startup ansible` resolves `.shared-roles` from the same workflow reference.
  Keep these three in step when the major version changes.
- Check README, CLI help, tests documentation and packaging comments when changing
  commands or release behavior. Git tags are the current public installation
  path; PyPI publishing is an optional manual workflow.
