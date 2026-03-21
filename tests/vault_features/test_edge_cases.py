"""
Tests for edge cases in Ansible vault secrets management.
"""

import tempfile
import shutil
from pathlib import Path
import subprocess

from cli.vault.fields import get_inline_vault_value

from .. import TEST_ROOT_PATH
from .utils import get_ansible_vault_cmd, get_dyscli_path


def test_nonexistent_file_handling():
    """Test handling of nonexistent files"""
    # GIVEN a path to a nonexistent vault file

    # Create a temporary directory for the test
    temp_dir = tempfile.mkdtemp()
    try:
        # Create a test data directory
        test_data_dir = Path(temp_dir) / "data"
        test_data_dir.mkdir(exist_ok=True)

        # Define a nonexistent file path
        nonexistent_file = "nonexistent_file.txt"
        test_password = "test"

        # WHEN the CLI is called to update that file
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
                nonexistent_file,
            ],
            capture_output=True,
            text=True,
        )

        # THEN an appropriate warning message is returned
        assert result.returncode == 0, f"Command failed unexpectedly: {result.stderr}"
        assert (
            "not found" in result.stdout.lower() or "not found" in result.stderr.lower()
        )

    finally:
        # Clean up
        shutil.rmtree(temp_dir)


def test_dry_run_mode():
    """Test dry run mode for updating secrets"""
    # GIVEN encrypted ansible vault files and fields
    get_ansible_vault_cmd()

    # Create a temporary directory for the test
    temp_dir = tempfile.mkdtemp()
    try:
        # Create a test data directory
        test_data_dir = Path(temp_dir) / "data"
        test_data_dir.mkdir(exist_ok=True)

        # Copy the existing all.yaml file to the test directory
        source_yaml = TEST_ROOT_PATH / "data" / "all.yaml"
        test_yaml = test_data_dir / "all.yaml"
        shutil.copy2(source_yaml, test_yaml)

        # Use the known vault password
        test_password = "test"

        # Get the initial value
        initial_backend_db_password = get_inline_vault_value(
            test_yaml, "backend_db_password", test_password
        )
        assert initial_backend_db_password is not None, "Failed to get initial value"

        # Store the content after encryption
        with open(test_yaml, "r") as f:
            initial_content = f.read()

        # WHEN the CLI is called with the dry-run flag
        dyscli_path = get_dyscli_path()

        result = subprocess.run(
            [
                "python",
                str(dyscli_path),
                "secrets",
                "update",
                "--repo",
                str(temp_dir),
                "--vault-password",
                test_password,
                "--set-field",
                "backend_db_password",
                "new_db_password",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
        )

        # THEN potential changes are shown but not actually applied to the files
        assert result.returncode == 0, f"Command failed: {result.stderr}"
        assert "dry-run" in result.stdout.lower() or "dry-run" in result.stderr.lower()

        # The original file should remain unchanged
        with open(test_yaml, "r") as f:
            current_content = f.read()
            assert current_content == initial_content

        # Check if the dry-run output directory was created
        dry_run_dir = Path("dry-run-output")
        assert dry_run_dir.exists(), "Dry-run output directory was not created"

        # Check if the dry-run output file contains the changes
        # Note: In the real code, the file is created at dry-run-output/data/all.yaml
        # matching the input path structure
        dry_run_file = dry_run_dir / "data" / "all.yaml"
        assert dry_run_file.exists(), "Dry-run output file was not created"

        # The dry-run file should have the new value
        dry_run_value = get_inline_vault_value(
            dry_run_file, "backend_db_password", test_password
        )
        assert dry_run_value is not None, "Failed to get dry-run value"
        assert dry_run_value == "new_db_password"
        assert dry_run_value != initial_backend_db_password

    finally:
        # Clean up
        shutil.rmtree(temp_dir)
        if Path("dry-run-output").exists():
            shutil.rmtree("dry-run-output")
