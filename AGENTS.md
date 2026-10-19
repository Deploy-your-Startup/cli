# Startup CLI contributor instructions

The `startup` CLI lets developers and coding agents ship a startup in their own
accounts. Product reference: [Deploy Your Startup](https://deploy-your-startup.com)
and its [quickstart](https://deploy-your-startup.com/get-started). The public
first-deploy path targets macOS, Hetzner, Django/FastAPI, Postgres and HTTPS;
verify support before claiming another stack or provider. Preserve user
ownership of code, infrastructure and deployment configuration, and keep the
shared workflow reusable across application stacks.

```bash
mise run format   # applies formatting and fixes
mise run lint     # only checks formatting, lint and types
mise run test     # pytest
mise run test-vault
```

## Where the rules live

| Topic | Source |
|---|---|
| Tests and completion claims | [Testing policy](docs/testing-policy.md) |
| Vault handling changes | [Ansible Vault compatibility](docs/vault-compatibility.md) |
| Shared clusters | [Shared startup clusters](docs/shared-clusters.md) |
| README, quickstarts, examples | [Writing public documentation](docs/public-documentation.md) |
| Terminal output | [Terminal design](docs/terminal-design.md) |
| Releases | [Releases](docs/releases.md) |

## Tests

- Run the real CLI as a subprocess (`python -m cli.startup ...`) or call a
  public entrypoint. For GitHub, put a fake `gh` on PATH backed by bare git
  repositories (`tests/test_sync_versioning_integration.py`).
- SSH commands run against a real SSH server in a local Docker container; only
  Kubernetes gets a stand-in (`tests/test_shared_cluster_data_integration.py`).
- Replace a nearby unit test of a routine helper with an integration scenario.

## Repository workflow

- Public command entrypoints live in `src/cli/startup.py`; implementations in
  the corresponding modules. Read the command and its tests before changing it.
- Use global uv, the committed `.python-version` and `mise -C <checkout> run
  <task>`. Pinned tools run through uv. Do not add git hooks.
- User commands stay independent of this checkout; installation provides its
  runtime dependencies.
- Bootstrap is resumable: preserve existing repositories, customizations and
  encrypted configuration.
- `startup sync` copies shared roles and workflows (default `main`, or
  `--source-version`); it does not advance role pins recorded with
  `startup ansible pin`. Keep workflow refs, role pins and checksums consistent.
  Keep it distinct from `startup template update`.
- The `deploy-your-startup` skill ships twice with identical content:
  `skills/deploy-your-startup/SKILL.md` and `src/cli/agent_skills/`. Update
  both; `tests/test_agent_skill_integration.py` fails on drift.
- When commands or release behavior change, check README, CLI help, test docs
  and packaging comments together. Git tags are the public installation path.

## Guardrails

- Never put credentials in command arguments, logs or committed files; secret
  examples use Keychain and stdin with an initial `--dry-run`.
- Require authorization for destructive operations.
- Browser setup and account-dependent operations need live verification; local
  tests do not establish a successful first deployment. Verify Actions and
  HTTPS before reporting a deployment as successful.
