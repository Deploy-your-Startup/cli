"""
Tests for updating encrypted fields in Ansible vault.
"""

import tempfile
import shutil
from pathlib import Path
import subprocess

from cli.vault.fields import get_inline_vault_value, update_inline_vault_field

from .. import TEST_ROOT_PATH
from .utils import get_ansible_vault_cmd, get_dyscli_path

# Import our new utility functions


def test_update_existing_field_with_random_value():
    """Test updating an existing field with a random value in a YAML file"""
    # GIVEN an ansible yaml file with existing vault fields
    get_ansible_vault_cmd()

    # Create a temporary directory for the test
    temp_dir = tempfile.mkdtemp()
    try:
        # Create a test data directory
        test_data_dir = Path(temp_dir) / "data"
        test_data_dir.mkdir(exist_ok=True)

        # Copy the existing all.yaml file to the test directory
        source_file = TEST_ROOT_PATH / "data" / "all.yaml"
        test_yaml = test_data_dir / "all.yaml"
        shutil.copy2(source_file, test_yaml)

        # Use the known vault password
        test_password = "test"

        # Get the initial value using our new utility function
        initial_backend_db_password = get_inline_vault_value(
            test_yaml, "backend_db_password", test_password
        )
        assert initial_backend_db_password is not None, "Failed to get initial value"

        # WHEN the CLI is called to update the field with a random value
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
                "backend_db_password",
            ],
            capture_output=True,
            text=True,
        )

        # THEN the field is updated with a new random value
        assert result.returncode == 0, f"Command failed: {result.stderr}"

        # Get the updated value using our new utility function
        updated_backend_db_password = get_inline_vault_value(
            test_yaml, "backend_db_password", test_password
        )
        assert updated_backend_db_password is not None, "Failed to get updated value"
        assert updated_backend_db_password != initial_backend_db_password
        assert len(updated_backend_db_password) > 0

    finally:
        # Clean up
        shutil.rmtree(temp_dir)


def test_update_multiple_fields_with_random_values():
    """Test updating multiple existing fields with random values in a YAML file"""
    # GIVEN an ansible yaml file with existing vault fields
    get_ansible_vault_cmd()

    # Create a temporary directory for the test
    temp_dir = tempfile.mkdtemp()
    try:
        # Create a test data directory
        test_data_dir = Path(temp_dir) / "data"
        test_data_dir.mkdir(exist_ok=True)

        # Copy the existing all.yaml file to the test directory
        source_file = TEST_ROOT_PATH / "data" / "all.yaml"
        test_yaml = test_data_dir / "all.yaml"
        shutil.copy2(source_file, test_yaml)

        # Use the known vault password
        test_password = "test"

        # Get the initial values using our utility function
        initial_backend_db_password = get_inline_vault_value(
            test_yaml, "backend_db_password", test_password
        )
        initial_postgres_admin_password = get_inline_vault_value(
            test_yaml, "postgres_admin_password", test_password
        )
        assert initial_backend_db_password is not None, (
            "Failed to get initial backend_db_password"
        )
        assert initial_postgres_admin_password is not None, (
            "Failed to get initial postgres_admin_password"
        )

        # WHEN the CLI is called to update multiple fields with random values
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
                "backend_db_password",
                "--vault-field",
                "postgres_admin_password",
            ],
            capture_output=True,
            text=True,
        )

        # THEN both fields are updated with new random values
        assert result.returncode == 0, f"Command failed: {result.stderr}"

        # Get the updated values using our utility function
        updated_backend_db_password = get_inline_vault_value(
            test_yaml, "backend_db_password", test_password
        )
        updated_postgres_admin_password = get_inline_vault_value(
            test_yaml, "postgres_admin_password", test_password
        )

        assert updated_backend_db_password is not None, (
            "Failed to get updated backend_db_password"
        )
        assert updated_postgres_admin_password is not None, (
            "Failed to get updated postgres_admin_password"
        )

        # Verify both values have changed
        assert updated_backend_db_password != initial_backend_db_password
        assert updated_postgres_admin_password != initial_postgres_admin_password

        # Verify both values are not empty
        assert len(updated_backend_db_password) > 0
        assert len(updated_postgres_admin_password) > 0

    finally:
        # Clean up
        shutil.rmtree(temp_dir)


def test_update_existing_field_with_specific_value():
    """Test updating an existing field with a specific value in a YAML file"""
    # GIVEN an ansible yaml file with existing vault fields
    get_ansible_vault_cmd()

    # Create a temporary directory for the test
    temp_dir = tempfile.mkdtemp()
    try:
        # Create a test data directory
        test_data_dir = Path(temp_dir) / "data"
        test_data_dir.mkdir(exist_ok=True)

        # Copy the existing all.yaml file to the test directory
        source_file = TEST_ROOT_PATH / "data" / "all.yaml"
        test_yaml = test_data_dir / "all.yaml"
        shutil.copy2(source_file, test_yaml)

        # Use the known vault password
        test_password = "test"
        specific_value = "my_specific_db_password"

        # WHEN the CLI is called to update the field with a specific value
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
                "--set-field",
                "backend_db_password",
                specific_value,
            ],
            capture_output=True,
            text=True,
        )

        # THEN the field is updated with the provided value
        assert result.returncode == 0, f"Command failed: {result.stderr}"

        # Get the updated value using our new utility function
        updated_backend_db_password = get_inline_vault_value(
            test_yaml, "backend_db_password", test_password
        )
        assert updated_backend_db_password is not None, "Failed to get updated value"
        assert updated_backend_db_password == specific_value

    finally:
        # Clean up
        shutil.rmtree(temp_dir)


def test_direct_update_with_utility_function():
    """Test directly updating a field using our utility function"""
    # GIVEN an ansible yaml file with existing vault fields

    # Create a temporary directory for the test
    temp_dir = tempfile.mkdtemp()
    try:
        # Create a test data directory
        test_data_dir = Path(temp_dir) / "data"
        test_data_dir.mkdir(exist_ok=True)

        # Copy the existing all.yaml file to the test directory
        source_file = TEST_ROOT_PATH / "data" / "all.yaml"
        test_yaml = test_data_dir / "all.yaml"
        shutil.copy2(source_file, test_yaml)

        # Use the known vault password
        test_password = "test"
        specific_value = "directly_updated_value"

        # Get the initial value
        initial_backend_db_password = get_inline_vault_value(
            test_yaml, "backend_db_password", test_password
        )
        assert initial_backend_db_password is not None, "Failed to get initial value"

        # WHEN we directly update the field using our utility function
        result = update_inline_vault_field(
            test_yaml, "backend_db_password", specific_value, test_password
        )

        # THEN the field is updated with the provided value
        assert result is True, "Failed to update field"

        # Get the updated value
        updated_backend_db_password = get_inline_vault_value(
            test_yaml, "backend_db_password", test_password
        )
        assert updated_backend_db_password is not None, "Failed to get updated value"
        assert updated_backend_db_password == specific_value
        assert updated_backend_db_password != initial_backend_db_password

    finally:
        # Clean up
        shutil.rmtree(temp_dir)


def test_update_field_with_direct_file_path():
    """Test updating a field in a specific file by providing the file path directly to --repo"""
    # GIVEN an ansible yaml file with existing vault fields

    # Create a temporary directory for the test
    temp_dir = tempfile.mkdtemp()
    try:
        # Create a test data directory
        test_data_dir = Path(temp_dir) / "data"
        test_data_dir.mkdir(exist_ok=True)

        # Copy the existing all.yaml file to the test directory
        source_file = TEST_ROOT_PATH / "data" / "all.yaml"
        test_yaml = test_data_dir / "all.yaml"
        shutil.copy2(source_file, test_yaml)

        # Use the known vault password
        test_password = "test"
        specific_value = "updated_via_direct_file_path"

        # Get the initial value
        initial_backend_db_password = get_inline_vault_value(
            test_yaml, "backend_db_password", test_password
        )
        assert initial_backend_db_password is not None, "Failed to get initial value"

        # WHEN the CLI is called with the direct file path as the repo
        dyscli_path = get_dyscli_path()

        result = subprocess.run(
            [
                "python",
                str(dyscli_path),
                "secrets",
                "update",
                "--repo",
                str(test_yaml),  # Direct file path instead of directory
                "--vault-password",
                test_password,
                "--set-field",
                "backend_db_password",
                specific_value,
            ],
            capture_output=True,
            text=True,
        )

        # THEN the field is updated with the provided value
        assert result.returncode == 0, f"Command failed: {result.stderr}"

        # Get the updated value
        updated_backend_db_password = get_inline_vault_value(
            test_yaml, "backend_db_password", test_password
        )
        assert updated_backend_db_password is not None, "Failed to get updated value"
        assert updated_backend_db_password == specific_value
        assert updated_backend_db_password != initial_backend_db_password

    finally:
        # Clean up
        shutil.rmtree(temp_dir)
