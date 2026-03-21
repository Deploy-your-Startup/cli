"""
Tests for performing multiple operations in a single command.
"""

import tempfile
import shutil
from pathlib import Path
import subprocess

from cli.vault.fields import get_inline_vault_value
from cli.vault.files import get_vault_file_content

from .. import TEST_ROOT_PATH
from .utils import get_ansible_vault_cmd, get_dyscli_path

# Import our new utility functions


def test_update_multiple_fields_and_files_in_single_command():
    """Test updating multiple fields and files with both random and specific values"""
    # GIVEN multiple vault files with different structures
    get_ansible_vault_cmd()

    # Create a temporary directory for the test
    temp_dir = tempfile.mkdtemp()
    try:
        # Create a test data directory
        test_data_dir = Path(temp_dir) / "data"
        test_data_dir.mkdir(exist_ok=True)

        # Copy the existing files to the test directory
        source_yaml = TEST_ROOT_PATH / "data" / "all.yaml"
        test_yaml = test_data_dir / "all.yaml"
        shutil.copy2(source_yaml, test_yaml)

        source_token = TEST_ROOT_PATH / "data" / "hcloud_token"
        test_token = test_data_dir / "hcloud_token"
        shutil.copy2(source_token, test_token)

        # Use the known vault password
        test_password = "test"

        # Get the initial values
        initial_backend_db_password = get_inline_vault_value(
            test_yaml, "backend_db_password", test_password
        )
        assert initial_backend_db_password is not None, (
            "Failed to get initial backend_db_password"
        )

        initial_postgres_admin_password = get_inline_vault_value(
            test_yaml, "postgres_admin_password", test_password
        )
        assert initial_postgres_admin_password is not None, (
            "Failed to get initial postgres_admin_password"
        )

        initial_token_content = get_vault_file_content(test_token, test_password)
        assert initial_token_content is not None, "Failed to get initial token content"

        # Define specific values
        specific_db_password = "new_specific_db_password"
        specific_token_content = "new_specific_token_content"

        # WHEN the CLI is called to update multiple fields and files
        dyscli_path = get_dyscli_path()

        result = subprocess.run(
            [
                "python",
                str(dyscli_path),
                "secrets",
                "update",
                "--repo",
                str(test_data_dir),
                "--vault-password",
                test_password,
                "--vault-field",
                "postgres_admin_password",  # Random value
                "--set-field",
                "backend_db_password",
                specific_db_password,  # Specific value
                "--set-file-content",
                "hcloud_token",
                specific_token_content,  # Specific file content
            ],
            capture_output=True,
            text=True,
        )

        # THEN all specified fields and files are updated
        assert result.returncode == 0, f"Command failed: {result.stderr}"

        # Check the updated values
        updated_backend_db_password = get_inline_vault_value(
            test_yaml, "backend_db_password", test_password
        )
        assert updated_backend_db_password is not None, (
            "Failed to get updated backend_db_password"
        )
        assert updated_backend_db_password == specific_db_password

        updated_postgres_admin_password = get_inline_vault_value(
            test_yaml, "postgres_admin_password", test_password
        )
        assert updated_postgres_admin_password is not None, (
            "Failed to get updated postgres_admin_password"
        )
        assert updated_postgres_admin_password != initial_postgres_admin_password
        assert len(updated_postgres_admin_password) > 0

        updated_token_content = get_vault_file_content(test_token, test_password)
        assert updated_token_content is not None, "Failed to get updated token content"
        assert updated_token_content == specific_token_content

    finally:
        # Clean up
        shutil.rmtree(temp_dir)


def test_rotate_multiple_files_in_single_command():
    """Test rotating vault keys for multiple files in a single command"""
    # GIVEN multiple vault files with different structures
    # Create a temporary directory for the test
    temp_dir = tempfile.mkdtemp()
    try:
        # Create a test data directory
        test_data_dir = Path(temp_dir) / "data"
        test_data_dir.mkdir(exist_ok=True)

        # Copy the existing files to the test directory
        source_yaml = TEST_ROOT_PATH / "data" / "all.yaml"
        test_yaml = test_data_dir / "all.yaml"
        shutil.copy2(source_yaml, test_yaml)

        source_token = TEST_ROOT_PATH / "data" / "hcloud_token"
        test_token = test_data_dir / "hcloud_token"
        shutil.copy2(source_token, test_token)

        # Use the known vault password and a new password
        old_password = "test"
        new_password = "new_test_password"

        # Get the initial values
        initial_backend_db_password = get_inline_vault_value(
            test_yaml, "backend_db_password", old_password
        )
        assert initial_backend_db_password is not None, (
            "Failed to get initial backend_db_password"
        )

        initial_token_content = get_vault_file_content(test_token, old_password)
        assert initial_token_content is not None, "Failed to get initial token content"

        # WHEN the CLI is called to rotate multiple files
        dyscli_path = get_dyscli_path()

        result = subprocess.run(
            [
                "python",
                str(dyscli_path),
                "secrets",
                "rotate-password",
                "--repo",
                str(test_data_dir),
                "--old-password",
                old_password,
                "--new-password",
                new_password,
            ],
            capture_output=True,
            text=True,
        )

        # THEN all files are rotated
        assert result.returncode == 0, f"Command failed: {result.stderr}"

        # Should not be able to decrypt with old password (using strict mode)
        old_db_password = get_inline_vault_value(
            test_yaml, "backend_db_password", old_password, strict=True
        )
        assert old_db_password is None, (
            "Should not be able to decrypt yaml with old password"
        )

        old_token_content = get_vault_file_content(
            test_token, old_password, strict=True
        )
        assert old_token_content is None, (
            "Should not be able to decrypt token with old password"
        )

        # Should be able to decrypt with new password
        new_db_password = get_inline_vault_value(
            test_yaml, "backend_db_password", new_password
        )
        assert new_db_password is not None, "Failed to decrypt yaml with new password"
        assert new_db_password == initial_backend_db_password

        new_token_content = get_vault_file_content(test_token, new_password)
        assert new_token_content is not None, (
            "Failed to decrypt token with new password"
        )
        assert new_token_content == initial_token_content

    finally:
        # Clean up
        shutil.rmtree(temp_dir)
