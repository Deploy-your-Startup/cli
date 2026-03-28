"""
Tests for updating encrypted files in Ansible vault.
"""

import tempfile
import shutil
from pathlib import Path
import subprocess

from cli.vault.files import (
    get_vault_file_content,
    update_vault_file,
    is_full_vault_file,
)


from .. import TEST_ROOT_PATH
from .utils import get_ansible_vault_cmd, get_dyscli_path


def test_update_encrypted_file_with_random_value():
    """Test updating an existing encrypted file with a random value"""
    # GIVEN an existing encrypted vault file
    get_ansible_vault_cmd()

    # Create a temporary directory for the test
    temp_dir = tempfile.mkdtemp()
    try:
        # Create a test data directory
        test_data_dir = Path(temp_dir) / "data"
        test_data_dir.mkdir(exist_ok=True)

        # Copy the existing hcloud_token file to the test directory
        source_file = TEST_ROOT_PATH / "data" / "hcloud_token"
        test_token_file = test_data_dir / "hcloud_token"
        shutil.copy2(source_file, test_token_file)

        # Use the known vault password
        test_password = "test"

        # Verify it's a vault file
        assert is_full_vault_file(test_token_file), "Not a vault file"

        # Get the initial content using our new utility function
        initial_content = get_vault_file_content(test_token_file, test_password)
        assert initial_content is not None, "Failed to get initial content"

        # WHEN the CLI is called to update the file with a random value
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
                "--vault-file",
                "hcloud_token",
            ],
            capture_output=True,
            text=True,
        )

        # THEN the file is updated with a new random value
        assert result.returncode == 0, f"Command failed: {result.stderr}"

        # Get the updated content using our new utility function
        updated_content = get_vault_file_content(test_token_file, test_password)
        assert updated_content is not None, "Failed to get updated content"
        assert updated_content != initial_content
        assert len(updated_content) > 0

    finally:
        # Clean up
        shutil.rmtree(temp_dir)


def test_update_encrypted_file_with_specific_value():
    """Test updating an existing encrypted file with a specific value"""
    # GIVEN an existing encrypted vault file
    get_ansible_vault_cmd()

    # Create a temporary directory for the test
    temp_dir = tempfile.mkdtemp()
    try:
        # Create a test data directory
        test_data_dir = Path(temp_dir) / "data"
        test_data_dir.mkdir(exist_ok=True)

        # Copy the existing hcloud_token file to the test directory
        source_file = TEST_ROOT_PATH / "data" / "hcloud_token"
        test_token_file = test_data_dir / "hcloud_token"
        shutil.copy2(source_file, test_token_file)

        # Use the known vault password
        test_password = "test"
        specific_content = "my_specific_token_content"

        # Verify it's a vault file
        assert is_full_vault_file(test_token_file), "Not a vault file"

        # WHEN the CLI is called to update the file with a specific value
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
                "--set-file-content",
                "hcloud_token",
                specific_content,
            ],
            capture_output=True,
            text=True,
        )

        # THEN the file is updated with the provided value
        assert result.returncode == 0, f"Command failed: {result.stderr}"

        # Get the updated content using our new utility function
        updated_content = get_vault_file_content(test_token_file, test_password)
        assert updated_content is not None, "Failed to get updated content"
        assert updated_content == specific_content

    finally:
        # Clean up
        shutil.rmtree(temp_dir)


def test_create_missing_encrypted_file_with_specific_value():
    """Test creating a new encrypted vault file when the target file is missing"""
    get_ansible_vault_cmd()

    temp_dir = tempfile.mkdtemp()
    try:
        test_data_dir = Path(temp_dir) / "data"
        test_data_dir.mkdir(exist_ok=True)

        test_password = "test"
        specific_content = "new_encrypted_token_content"
        test_token_file = test_data_dir / "hcloud_token"

        assert not test_token_file.exists()

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
                "--set-file-content",
                "hcloud_token",
                specific_content,
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"Command failed: {result.stderr}"
        assert test_token_file.exists(), "Vault file was not created"
        assert is_full_vault_file(test_token_file), "Created file is not vaulted"

        updated_content = get_vault_file_content(test_token_file, test_password)
        assert updated_content == specific_content

    finally:
        shutil.rmtree(temp_dir)


def test_direct_update_with_utility_function():
    """Test directly updating a file using our utility function"""
    # GIVEN an existing encrypted vault file

    # Create a temporary directory for the test
    temp_dir = tempfile.mkdtemp()
    try:
        # Create a test data directory
        test_data_dir = Path(temp_dir) / "data"
        test_data_dir.mkdir(exist_ok=True)

        # Copy the existing hcloud_token file to the test directory
        source_file = TEST_ROOT_PATH / "data" / "hcloud_token"
        test_token_file = test_data_dir / "hcloud_token"
        shutil.copy2(source_file, test_token_file)

        # Use the known vault password
        test_password = "test"
        specific_content = "directly_updated_content"

        # Verify it's a vault file
        assert is_full_vault_file(test_token_file), "Not a vault file"

        # Get the initial content
        initial_content = get_vault_file_content(test_token_file, test_password)
        assert initial_content is not None, "Failed to get initial content"

        # WHEN we directly update the file using our utility function
        result = update_vault_file(test_token_file, specific_content, test_password)

        # THEN the file is updated with the provided value
        assert result is True, "Failed to update file"

        # Get the updated content
        updated_content = get_vault_file_content(test_token_file, test_password)
        assert updated_content is not None, "Failed to get updated content"
        assert updated_content == specific_content
        assert updated_content != initial_content

    finally:
        # Clean up
        shutil.rmtree(temp_dir)


def test_update_multiple_encrypted_files_with_random_values():
    """Test updating multiple existing encrypted files with random values"""
    # GIVEN multiple existing encrypted vault files
    get_ansible_vault_cmd()

    # Create a temporary directory for the test
    temp_dir = tempfile.mkdtemp()
    try:
        # Create a test data directory
        test_data_dir = Path(temp_dir) / "data"
        test_data_dir.mkdir(exist_ok=True)

        # Copy the existing hcloud_token file to the test directory
        source_token = TEST_ROOT_PATH / "data" / "hcloud_token"
        test_token_file = test_data_dir / "hcloud_token"
        shutil.copy2(source_token, test_token_file)

        # Create a second vault file
        second_vault_file = test_data_dir / "second_vault_file"
        with open(second_vault_file, "w") as f:
            f.write("initial_second_vault_content")

        # Encrypt the second file using a temporary password file
        password_file = test_data_dir / "vault_password.txt"
        with open(password_file, "w") as f:
            f.write("test")

        subprocess.run(
            [
                "uv",
                "run",
                "ansible-vault",
                "encrypt",
                "--vault-password-file",
                str(password_file),
                str(second_vault_file),
            ],
            check=True,
        )

        # Use the known vault password
        test_password = "test"

        # Verify they're vault files
        assert is_full_vault_file(test_token_file), "First file is not a vault file"
        assert is_full_vault_file(second_vault_file), "Second file is not a vault file"

        # Get the initial content using our utility function
        initial_token_content = get_vault_file_content(test_token_file, test_password)
        assert initial_token_content is not None, "Failed to get initial token content"

        initial_second_content = get_vault_file_content(
            second_vault_file, test_password
        )
        assert initial_second_content is not None, (
            "Failed to get initial second content"
        )

        # WHEN the CLI is called to update multiple files with random values
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
                "--vault-file",
                "hcloud_token",
                "--vault-file",
                "second_vault_file",
            ],
            capture_output=True,
            text=True,
        )

        # THEN all files are updated with new random values
        assert result.returncode == 0, f"Command failed: {result.stderr}"

        # Get the updated content
        updated_token_content = get_vault_file_content(test_token_file, test_password)
        assert updated_token_content is not None, "Failed to get updated token content"
        assert updated_token_content != initial_token_content
        assert len(updated_token_content) > 0

        updated_second_content = get_vault_file_content(
            second_vault_file, test_password
        )
        assert updated_second_content is not None, (
            "Failed to get updated second content"
        )
        assert updated_second_content != initial_second_content
        assert len(updated_second_content) > 0

    finally:
        # Clean up
        shutil.rmtree(temp_dir)
