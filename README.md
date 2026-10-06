# Deploy Your Startup CLI

A command-line tool for managing Deploy Your Startup operations, including secrets management, GitHub repository deployment, and more.

Source of truth for the product vision: [Deploy Your Startup](https://deploy-your-startup.com).

## Features

- **Secrets Management**: Manage Ansible Vault secrets with easy commands
- **GitHub Deployment**: Create new GitHub repositories from templates
- **Template Sync**: Sync the shared `deploy-template` repository into your GitHub account
- **Vault Rotation**: Rotate vault passwords across multiple files

## Installation and first project (early access)

Start on macOS with Git, Chrome, a GitHub account, a Hetzner Cloud account and a
domain you own. Servers and domains are billed by your providers.

```bash
curl -fsSL https://deploy-your-startup.com/install.sh | bash -s -- --onboard
```

The installer provides Python 3.14 and CLI `v0.1.3`, connects GitHub and checks
your setup. The wizard asks for your project name and domain, then shows a launch
plan before creating anything. Extra domains and error tracking are optional.
Interactive terminals show a brief launch animation once, then stop. Set
`STARTUP_NO_ANIMATION=1` to disable it; redirected output, CI and `NO_COLOR` stay static.
The default application template is pinned to `v0.1.0` independently of the CLI.

If Git is missing, run `xcode-select --install` first. The terminal explains any
missing prerequisites. To resume the wizard after installation:

```bash
startup doctor
startup bootstrap --kind fullstack --provider hetzner --domain-owned --template-version v0.1.0
```

The first-deploy path creates Django/FastAPI, Postgres and HTTPS on k3s in your
own accounts. An existing shared deployment repository is preserved. Advanced
options, including Vue templates, are available through `startup bootstrap --help`.

Bootstrap requests a deployment; it does not prove the site is live. Follow the
Actions link, point DNS at your server when needed, then verify your site over
HTTPS. For local development, follow the generated project's README.

## Use the CLI from a coding agent

The CLI ships an agent skill that teaches Claude Code, Codex and OpenCode how
to install, set up and operate `startup`: bootstrap, deployment verification,
secrets and updates, including when to ask before costly or destructive steps.
Install it for every agent found in your home directory:

```bash
startup skills install
```

Use `--agent claude|codex|opencode` to choose agents and `--dry-run` to preview
the paths. Re-run it after updating the CLI so the skill matches your version.
Then start a new agent session and ask it, for example, to launch a landing page
for your domain.

Without the CLI installed yet, add the same skill with a skill installer or as a
Claude Code plugin:

```bash
npx skills add Deploy-your-Startup/cli --skill deploy-your-startup
```

```text
/plugin marketplace add Deploy-your-Startup/cli
/plugin install deploy-your-startup@deploy-your-startup
```

The agent offers the official installer when `startup` is missing.

## Update or remove the CLI

Run the installer again to install the currently published CLI release:

```bash
curl -fsSL https://deploy-your-startup.com/install.sh | bash
```

This updates the CLI only. Use `startup sync` for shared deployment workflows and
roles, and `startup template update` for application template changes.

To remove the managed CLI installation:

```bash
curl -fsSL https://deploy-your-startup.com/uninstall.sh | bash
```

Removal asks for confirmation and preserves projects, Keychain credentials and
cloud resources. Servers continue running and billing until you remove them
separately. Installations made directly with uv are managed with `uv tool uninstall deploy-your-startup-cli`; the website uninstaller handles its own installations.

To install a checkout for CLI development:

```bash
uv tool install --reinstall deploy-your-startup-cli --from .
```

## Usage

The CLI provides several command groups:

### Secrets Management

The CLI provides comprehensive vault secret management with two main operation types:

#### Update Inline Vault Fields

Update specific fields in YAML files that contain inline vault blocks (`field: !vault | ...`):

```bash
# Generate random value for a field
startup secrets update -r . -p PASSWORD --field-random backend_db_password

# Set specific value for a field
startup secrets update -r . -p PASSWORD --field-set api_key "my-secret-key"

# Update multiple fields at once
startup secrets update -r . -p PASSWORD \
  --field-random db_password \
  --field-random api_secret \
  --field-set admin_email "admin@example.com"

# Update a specific file directly
startup secrets update -r path/to/file.yml -p PASSWORD --field-random db_pass

# Preview changes without applying (dry run)
startup secrets update -r . -p PASSWORD --field-random db_pass --dry-run
```

#### Work with Full Vault Files

Operate on completely encrypted vault files:

```bash
# Re-encrypt a vault file (rotate encryption with same password)
startup secrets update -r . -p PASSWORD --file-rotate secrets.yml

# Replace content of a vault file
startup secrets update -r . -p PASSWORD --file-content secrets.yml "new content"
```

#### Other Vault Operations

```bash
# Rotate vault password across all files
startup secrets rotate-password --repo PATH --old-password OLD --new-password NEW

# List all vaulted files in a repository
startup secrets list-vaults --repo PATH

# Get decrypted value of a specific vault field
# --vault-password is optional; omit it to read from the keychain
startup secrets get-field --file PATH --field FIELD_NAME

# Update a specific inline vault field directly
startup secrets update-inline-field --file PATH --field FIELD_NAME --value NEW_VALUE
```

#### Backward Compatibility

The old parameter names are still supported but deprecated:
- `--vault-field` → use `--field-random`
- `--set-field` → use `--field-set`
- `--vault-file` → use `--file-rotate`
- `--set-file-content` → use `--file-content`

### GitHub Deployment

```bash
# Create a new GitHub repository from a template
startup deploy create --repo-name REPO_NAME

# Alternative command
startup deploy github --repo-name REPO_NAME --repo-description "My new project"
```

### Template Sync

Sync the shared template repositories into your own GitHub account using your local `gh` login:

```bash
# Sync the shared deploy repo
startup sync

# Preview changes without commit/push
startup sync --dry-run
```

Defaults:
- `Deploy-your-Startup/deploy-template` -> `<your-user>/deploy-your-startup`

When you sync the deploy repository as a private repo, `startup` also enables the
GitHub Actions access mode that allows private actions and reusable workflows to
be consumed by other private repositories owned by the same user.

You can override source and target names if needed:

```bash
startup sync --owner philipp-lein --repo-name deploy-your-startup
startup sync --source-owner Deploy-your-Startup --source-repo deploy-template
```

### Shared Deploy Workflow

The recommended setup is now:

1. Sync `Deploy-your-Startup/deploy-template` to your own private
   `<owner>/deploy-your-startup` repository with `startup sync`
2. Let project repositories call reusable workflows and composite actions from
   that synced `deploy-your-startup` repository

This avoids per-repository secrets for shared roles access in GitHub Actions and
keeps shared CI logic and shared Ansible roles in one place.

In CI, the shared deploy action exports the bundled roles into
`deployment/.shared-roles` before `startup` runs. The CI workflow then calls
`startup` with
`--no-refresh` so the exported copy is reused instead of trying to clone the
shared repository again.

Locally, keep the default `--refresh` behavior. The first Git-backed setup records
`shared-roles.ref` and `shared-roles.sha256`; review and commit both files.
Subsequent runs reuse that exact commit. CI exports must match its checksum.
To update roles, first sync the shared deployment repository, then run
`startup ansible pin --working-directory deployment --version <reviewed-ref>`.
Review and commit both new pin files, and ensure CI exports the same roles.
`startup sync` alone does not advance an existing project's pin.

Examples:

```bash
# One-time setup for a user account
startup sync --owner philipp-lein

# Local deployment refreshes Git while preserving the reviewed pin
uv run startup ansible setup_ansible --working-directory .

# CI-style reuse of a pre-exported .shared-roles directory
uv run startup ansible setup_ansible --working-directory . --no-refresh
```

### Backup And Restore

```bash
# Default behavior: use the macOS Keychain when no vault password is passed
uv run startup ansible deploy --working-directory deployment --environment production --service backend
uv run startup ansible kubeconfig --working-directory deployment --environment production

# Create a production backup on your local machine
uv run startup ansible backup --working-directory deployment --environment production --vault-password PASSWORD

# Restore the latest backup set back into the cluster
uv run startup ansible restore --working-directory deployment --environment production --vault-password PASSWORD --yes

# Restore a specific backup directory or only one artifact type
uv run startup ansible restore --working-directory deployment --environment production --vault-password PASSWORD --backup-dir ~/Backups/about-phil/2026-03-22_16-28-00 --yes
uv run startup ansible restore --working-directory deployment --environment production --vault-password PASSWORD --db-file ~/Backups/about-phil/...sql.gz --no-restore-media --yes
```

When no `--vault-password` is passed, `startup` reads the password from the configured vault password backend (default: the macOS Keychain). The Keychain backend derives the service name from the project directory, for example `VAULT_PASSWORD_ABOUT_PHIL` or `VAULT_PASSWORD_GAMING_BUCH_CLUB`. Backends are pluggable — see `src/cli/vault_backends.py`; today only `keychain` ships.

Project repositories can wrap this with local commands such as:

```bash
cd deployment
./make.sh deploy --environment production --service backend
./make.sh kubeconfig --environment production
./make.sh backup --environment production
```

Use `scripts/secrets.sh store <project>` from the portfolio hub once to create the Keychain entry if it does not exist yet.

### VM Updates

```bash
# Update all package-managed hosts in production
uv run startup ansible update-vms --working-directory deployment --environment production --vault-password PASSWORD

# Limit updates to a subset of hosts and reboot if the OS requests it
uv run startup ansible update-vms --working-directory deployment --environment production --vault-password PASSWORD --limit workers --reboot
```

## Development

### Quick Start

Use the provided `make.sh` script for common development tasks:

```bash
# Show all available commands
./make.sh help

# Set up local development environment (installs uv, ruff, and the CLI)
./make.sh setup_local

# Format code and run linting
./make.sh format

# Run tests
./make.sh test

# Install CLI as a global tool
./make.sh install_tool

# Install in development mode
./make.sh dev_install

# Clean build artifacts
./make.sh clean
```

### Manual Setup

1. Clone the repository:
   ```bash
   git clone https://github.com/Deploy-your-Startup/cli.git
   cd cli
   ```

2. Install in development mode:
   ```bash
   uv pip install -e .
   ```

### Running Tests

```bash
# Using make.sh
./make.sh test

# Or directly with uv
uv run pytest

# Run specific test file
uv run pytest tests/test_deploy.py

# Run with verbose output
uv run pytest -v
```

### Code Formatting

```bash
# Using make.sh
./make.sh format

# Or directly with uvx
uv run --extra dev ruff format
uv run --extra dev ruff check --fix
```

## Project Structure

```
cli/
├── src/
│   └── cli/
│       ├── startup.py          # Main CLI entry point
│       ├── deploy.py          # GitHub deployment commands
│       ├── rotate_vault.py    # Vault rotation utilities
│       ├── update_vault_secrets.py  # Vault update utilities
│       ├── skill_commands.py  # `startup skills` for coding agents
│       ├── agent_skills/      # Agent skill bundled with the package
│       └── vault/             # Vault management modules
├── skills/                    # Same agent skill for skill installers
├── .claude-plugin/            # Claude Code plugin and marketplace manifests
├── tests/                     # Test suite
├── pyproject.toml            # Project configuration
├── make.sh                   # Development helper script
└── README.md                 # This file
```

## Requirements

- Python >= 3.12
- uv (recommended for installation and development)

## License

MIT

## Application template updates (Copier)

Full-stack `startup bootstrap` renders the Django template with Copier. Select
an explicit template using `--template <git-url-or-local-repo>` and
`--template-version <tag-or-commit>` when reviewing a release. Pitch templates
and `startup sync` retain their existing workflows.

Existing projects can record a baseline without changing their files:

```bash
startup template adopt --project-dir ./my-startup --version <template-tag-or-commit>
```

The command reads public project parameters from group_vars and the Git remote.
Override a public field with `--data base_domain=example.com`. Private keys,
tokens and other secret fields are not accepted. Review and commit the generated
`.copier-answers.yml`. Adoption treats existing differences and deleted template
files as project customizations; it does not apply historical template changes.

Then preview and apply the same target version:

```bash
startup template update --project-dir ./my-startup --version <template-tag-or-commit> --dry-run
startup template update --project-dir ./my-startup --version <template-tag-or-commit>
```

`--dry-run` performs a real update in a temporary Git clone and displays tracked
diffs and newly added file names. Both commands require a clean repository.
Conflicts produce a failing exit status and remain visible for manual resolution.
The commands do not commit, push, deploy or decrypt Vault; existing group_vars
are preserved. Run the affected project's checks before committing an update.

The default update target is the template's HEAD. For repeatable rollouts across
multiple projects, explicitly pass a tested tag or commit. Copier is pinned in
the CLI's runtime dependencies.

Optional login for templates with OAuth2 Proxy: install the official `auth0` CLI,
run `auth0 login` for your tenant, then pass `--auth0-tenant <tenant>` to bootstrap.
For an existing project: `startup auth0 setup --tenant <tenant> --project-name <name> --base-domain <domain>`.
Templates declare optional Auth0 support in `startup-template.yml`; bootstrap asks
for the tenant and validates CLI access before provisioning. With `--yes`, pass
`--auth0-tenant <tenant>` or explicitly `--without-auth`.
After deployment: `startup auth0 validate --tenant <tenant> --base-domain <domain>`
opens a temporary browser for a real login and checks the protected API and secure
HttpOnly session. No passwords, tokens or browser state are printed or saved.

## Maintainer releases

See [CLI releases](docs/releases.md) for tags, verification, installer updates
and optional PyPI publishing.
