"""
Tests for rotating vault keys in Ansible vault.
"""

import tempfile
import shutil
from pathlib import Path
import subprocess

from cli.vault.fields import get_inline_vault_value
from cli.vault.files import get_vault_file_content, rotate_full_vault_file

from .. import TEST_ROOT_PATH
from .utils import get_ansible_vault_cmd, get_dyscli_path


def test_rotate_vault_key_for_inline_fields():
    """Test rotating vault key for inline fields in a YAML file"""
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

        # Use the known vault password and a new password
        old_password = "test"
        new_password = "new_test_password"

        # Get the initial value using our utility function
        initial_backend_db_password = get_inline_vault_value(
            test_yaml, "backend_db_password", old_password
        )
        assert initial_backend_db_password is not None, "Failed to get initial value"

        # WHEN the CLI is called to rotate the vault key
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
                "--file",
                "all.yaml",
            ],
            capture_output=True,
            text=True,
        )

        # THEN the yaml file remains encrypted but with the new key
        assert result.returncode == 0, f"Command failed: {result.stderr}"

        # Should not be able to decrypt with old password (using strict mode)
        print("\n=== TESTING DECRYPTION WITH OLD PASSWORD (SHOULD FAIL) ===")
        # Force a direct test with ansible-vault command
        with tempfile.NamedTemporaryFile(delete=False, mode="w") as pwd_file:
            pwd_file.write(old_password)
            pwd_file_path = pwd_file.name

        # Extract the vault block
        content = test_yaml.read_text(encoding="utf-8")
        from src.cli.vault.fields import extract_vault_block

        vault_block = extract_vault_block(content, "backend_db_password")

        # Write the vault block to a temporary file
        with tempfile.NamedTemporaryFile(delete=False, mode="w") as vault_file:
            vault_file.write(vault_block)
            vault_file_path = vault_file.name

        # Try to decrypt with old password using ansible-vault directly
        result = subprocess.run(
            [
                "ansible-vault",
                "view",
                "--vault-password-file",
                pwd_file_path,
                vault_file_path,
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        print(f"Decryption with old password result code: {result.returncode}")
        print(f"Stdout: {result.stdout}")
        print(f"Stderr: {result.stderr}")

        # Clean up temporary files
        Path(pwd_file_path).unlink()
        Path(vault_file_path).unlink()

        # Now use our function
        old_decrypted = get_inline_vault_value(
            test_yaml, "backend_db_password", old_password, verbose=True, strict=True
        )
        print(f"Old decrypted value: {old_decrypted}")
        assert old_decrypted is None, "Should not be able to decrypt with old password"

        # Should be able to decrypt with new password
        new_decrypted = get_inline_vault_value(
            test_yaml, "backend_db_password", new_password
        )
        assert new_decrypted is not None, "Failed to decrypt with new password"
        assert new_decrypted == initial_backend_db_password

    finally:
        # Clean up
        shutil.rmtree(temp_dir)


def test_rotate_vault_key_for_full_files():
    """Test rotating vault key for fully encrypted files"""
    # GIVEN a fully encrypted vault file
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

        # Use the known vault password and a new password
        old_password = "test"
        new_password = "new_test_password"

        # Get the initial content using our utility function
        initial_content = get_vault_file_content(test_token_file, old_password)
        assert initial_content is not None, "Failed to get initial content"

        # WHEN the CLI is called to rotate the vault key
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
                "--file",
                "hcloud_token",
            ],
            capture_output=True,
            text=True,
        )

        # THEN the file remains encrypted but with the new key
        assert result.returncode == 0, f"Command failed: {result.stderr}"

        # Should not be able to decrypt with old password (using strict mode)
        print("\n=== TESTING FULL FILE DECRYPTION WITH OLD PASSWORD (SHOULD FAIL) ===")
        # Force a direct test with ansible-vault command
        with tempfile.NamedTemporaryFile(delete=False, mode="w") as pwd_file:
            pwd_file.write(old_password)
            pwd_file_path = pwd_file.name

        # Try to decrypt with old password using ansible-vault directly
        result = subprocess.run(
            [
                "ansible-vault",
                "view",
                "--vault-password-file",
                pwd_file_path,
                str(test_token_file),
            ],
            capture_output=True,
            text=True,
            check=False,
        )

        print(f"Decryption with old password result code: {result.returncode}")
        print(f"Stdout: {result.stdout}")
        print(f"Stderr: {result.stderr}")

        # Clean up temporary files
        Path(pwd_file_path).unlink()

        # Now use our function
        old_decrypted = get_vault_file_content(
            test_token_file, old_password, strict=True
        )
        print(f"Old decrypted value: {old_decrypted}")
        assert old_decrypted is None, "Should not be able to decrypt with old password"

        # Should be able to decrypt with new password
        new_decrypted = get_vault_file_content(test_token_file, new_password)
        assert new_decrypted == initial_content

    finally:
        # Clean up
        shutil.rmtree(temp_dir)


def test_direct_rotate_with_utility_function():
    """Test directly rotating a vault file using our utility function"""
    # GIVEN a fully encrypted vault file

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

        # Use the known vault password and a new password
        old_password = "test"
        new_password = "new_test_password"

        # Get the initial content
        initial_content = get_vault_file_content(test_token_file, old_password)
        assert initial_content is not None, "Failed to get initial content"

        # WHEN we directly rotate the vault key using our utility function
        result = rotate_full_vault_file(test_token_file, old_password, new_password)

        # THEN the file is rotated successfully
        assert result is True, "Failed to rotate vault key"

        # Should not be able to decrypt with old password (using strict mode)
        old_decrypted = get_vault_file_content(
            test_token_file, old_password, strict=True
        )
        assert old_decrypted is None, "Should not be able to decrypt with old password"

        # Should be able to decrypt with new password
        new_decrypted = get_vault_file_content(test_token_file, new_password)
        assert new_decrypted is not None, "Failed to decrypt with new password"
        assert new_decrypted == initial_content

    finally:
        # Clean up
        shutil.rmtree(temp_dir)
