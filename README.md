# Deploy Your Startup CLI

A command-line tool for managing Deploy Your Startup operations, including secrets management, GitHub repository deployment, and more.

## Features

- **Secrets Management**: Manage Ansible Vault secrets with easy commands
- **GitHub Deployment**: Create new GitHub repositories from templates
- **Template Sync**: Sync the shared `deploy-template` repository into your GitHub account
- **Vault Rotation**: Rotate vault passwords across multiple files

## Installation

### Recommended: Global Tool Installation with uv

Install the CLI as a global tool using uv (recommended):

```bash
uv tool install deploy-your-startup-cli --from cli
```

Or install from the current directory:

```bash
cd cli
uv tool install deploy-your-startup-cli --from .
```

After installation, the `startup` command will be available globally.

### Alternative: pip Installation

You can also install using pip:

```bash
pip install deploy-your-startup-cli
```

Or with uv pip:

```bash
uv pip install deploy-your-startup-cli
```

### Optional Dependencies

The CLI has optional dependency groups for specific use cases:

```bash
# Install with development dependencies (pytest, ruff)
uv pip install "deploy-your-startup-cli[dev]"

# Install with server dependencies (fastapi, uvicorn)
uv pip install "deploy-your-startup-cli[server]"

# Install with all optional dependencies
uv pip install "deploy-your-startup-cli[dev,server]"
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
startup secrets get-field --file PATH --field FIELD_NAME --vault-password PASSWORD

# Update a specific inline vault field directly
startup secrets update-inline-field --file PATH --field FIELD_NAME --value NEW_VALUE --vault-password PASSWORD
```

#### Backward Compatibility

The old parameter names are still supported but deprecated:
- `--vault-field` → use `--field-random`
- `--set-field` → use `--field-set`
- `--vault-file` → use `--file-rotate`
- `--set-file-content` → use `--file-content`
```

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

Locally, keep the default `--refresh` behavior so `startup` can still update the
shared deploy checkout from git.

Examples:

```bash
# One-time setup for a user account
startup sync --owner philipp-lein

# Local deployment keeps refreshing the shared checkout
uv run startup ansible setup_ansible --working-directory .

# CI-style reuse of a pre-exported .shared-roles directory
uv run startup ansible setup_ansible --working-directory . --no-refresh
```

### Backup And Restore

```bash
# Create a production backup on your local machine
uv run startup ansible backup --working-directory deployment --environment production --vault-password PASSWORD

# Restore the latest backup set back into the cluster
uv run startup ansible restore --working-directory deployment --environment production --vault-password PASSWORD --yes

# Restore a specific backup directory or only one artifact type
uv run startup ansible restore --working-directory deployment --environment production --vault-password PASSWORD --backup-dir ~/Backups/about-phil/2026-03-22_16-28-00 --yes
uv run startup ansible restore --working-directory deployment --environment production --vault-password PASSWORD --db-file ~/Backups/about-phil/...sql.gz --no-restore-media --yes
```

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
uvx ruff format
uvx ruff check --fix
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
│       └── vault/             # Vault management modules
├── tests/                     # Test suite
├── pyproject.toml            # Project configuration
├── make.sh                   # Development helper script
└── README.md                 # This file
```

## Requirements

- Python >= 3.11
- uv (recommended for installation and development)

## License

MIT
