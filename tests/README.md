# Vault Secret Management Tests

This directory contains tests for the vault secret management functionality.

## Running the Tests

You can run all tests with pytest:

```bash
# From the cli directory
uv run python -m pytest -v tests/test_update_secrets.py
```

To run a specific test:

```bash
# From the cli directory
uv run python -m pytest -v tests/test_update_secrets.py::test_rotate_single_field
uv run python -m pytest -v tests/test_update_secrets.py::test_set_specific_field_value
uv run python -m pytest -v tests/test_update_secrets.py::test_set_file_content
```

## Test Cases

The tests cover the following functionality:

1. **Rotating a Single Field** - Testing the generation of a random value for a field in a vault file
2. **Setting a Specific Field Value** - Testing the `--set-field` option to set a specific value
3. **Setting File Content** - Testing the `--set-file-content` option to replace an entire vault file

## Manual Testing

You can also test the functionality manually:

### 1. Rotate Fields with Random Values

```bash
# From the project root
./cli/make.sh update-secrets \
  --vault-password your_vault_password \
  --repo test_repo \
  --vault-fields backend_db_password api_key \
  --verbose
```

### 2. Set Specific Field Values

```bash
# From the project root
./cli/make.sh update-secrets \
  --vault-password your_vault_password \
  --repo test_repo \
  --set-field backend_db_password secure_password123 \
  --set-field api_key your_api_key_here \
  --verbose
```

### 3. Update Entire File Content

```bash
# From the project root
./cli/make.sh update-secrets \
  --vault-password your_vault_password \
  --repo test_repo \
  --set-file-content hcloud_token "your-hetzner-cloud-token-content" \
  --verbose
```

### 4. Rotate Full Vault Files (re-encrypt with same password)

```bash
# From the project root
./cli/make.sh update-secrets \
  --vault-password your_vault_password \
  --repo test_repo \
  --vault-files hcloud_token_production ci_ssh_key \
  --verbose
```

## Creating Test Data

You can create test vault files for manual testing with:

```bash
# Create a test directory
mkdir -p test_vault_data/data

# Create a simple YAML file
echo "backend_db_password: initial_password" > test_vault_data/data/all.yml

# Create a token file
echo "initial_token_content" > test_vault_data/data/hcloud_token

# Encrypt them with ansible-vault (using uv run)
uv run ansible-vault encrypt --vault-password-file <(echo "test_password") test_vault_data/data/all.yml
uv run ansible-vault encrypt --vault-password-file <(echo "test_password") test_vault_data/data/hcloud_token
```

Then you can run update operations on these test files:

```bash
# From the project root
./cli/make.sh update-secrets \
  --vault-password test_password \
  --repo test_vault_data \
  --set-field backend_db_password new_secure_password \
  --verbose
```

## Combining Multiple Operations

You can combine multiple operations in a single command:

```bash
./cli/make.sh update-secrets \
  --vault-password your_vault_password \
  --repo test_repo \
  --set-field backend_db_password secure_password123 \
  --vault-fields api_key \
  --set-file-content hcloud_token "your-token-content" \
  --vault-files another_vault_file \
  --verbose
```

This will:
1. Set `backend_db_password` to "secure_password123"
2. Generate a random value for `api_key`
3. Replace the content of `hcloud_token` with "your-token-content"
4. Rotate (re-encrypt) `another_vault_file`

## Additional Options

- `--dry-run` - Preview changes without applying them
- `--only-existing` - Only update existing vault entries
- `--verify-password` - Verify vault password can decrypt existing secrets