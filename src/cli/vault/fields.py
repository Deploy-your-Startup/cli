"""
Utilities for handling inline encrypted fields in Ansible vault.
"""

import re
import subprocess
import tempfile
import logging
from pathlib import Path
from tempfile import NamedTemporaryFile
from ansible.parsing.vault import VaultLib, VaultSecret, VaultEditor

from .common import verify_vault_password, create_vault_lib

DEFAULT_VAULT_IDENTITY = "default"

# Set up logger
logger = logging.getLogger(__name__)


def extract_vault_block(content, var_name):
    """
    Extract the vault block for a variable name.

    Args:
        content (str): The content to search in
        var_name (str): The variable name to extract the vault block for

    Returns:
        str or None: The extracted vault block, or None if not found
    """
    # Find the line with the variable name and !vault marker
    var_pattern = rf"^[ \t]*{re.escape(var_name)}:\s*!vault \|"
    var_match = re.search(var_pattern, content, re.MULTILINE)
    if not var_match:
        return None

    # Get the content after the match
    lines = content[var_match.end() :].strip().split("\n")
    vault_lines = []
    base_indent = None

    # Skip any empty lines at the beginning
    start_idx = 0
    while start_idx < len(lines) and not lines[start_idx].strip():
        start_idx += 1

    if start_idx >= len(lines):
        return None

    # The first line should be the vault header
    first_line = lines[start_idx].strip()
    if not first_line.startswith("$ANSIBLE_VAULT;"):
        return None

    # Get the base indentation from the first content line
    base_indent = len(lines[start_idx]) - len(lines[start_idx].lstrip())

    # Start with the vault header
    vault_lines.append(first_line)

    # Process the rest of the lines
    for i in range(start_idx + 1, len(lines)):
        line = lines[i]
        stripped = line.strip()

        # Skip empty lines
        if not stripped:
            continue

        # If indentation decreases, we're out of the block
        current_indent = len(line) - len(line.lstrip())
        if current_indent < base_indent:
            break

        # Check if we've hit a line that doesn't look like hex content
        # Only hex digits are valid in ansible vault content
        if not all(c in "0123456789abcdefABCDEF" for c in stripped):
            break

        vault_lines.append(stripped)

    if len(vault_lines) <= 1:  # We only found the header
        return None

    return "\n".join(vault_lines)


def replace_block(content, var_name, new_block):
    """
    Replace all contiguous vault blocks for var_name, ensuring consistent indentation.

    Args:
        content (str): The content to replace in
        var_name (str): The variable name to replace the vault block for
        new_block (str): The new vault block

    Returns:
        tuple: (new_content, count) - The new content and the number of replacements
    """

    def _repl(match):
        indent = match.group("indent")
        # Split the new_block into lines
        lines = new_block.rstrip("\n").split("\n")

        # Extract just the variable name and vault marker from the first line
        # The format is typically "var_name: !vault |"
        first_line_parts = lines[0].split(":", 1)
        if len(first_line_parts) == 2:
            var_part = first_line_parts[0]
            vault_marker = first_line_parts[1].strip()
            # Reconstruct the first line with proper indentation
            result = [f"{indent}{var_part}: {vault_marker}"]

            # For all vault content lines, use consistent indentation (2 spaces)
            for line in lines[1:]:
                result.append(indent + "  " + line.strip())

            return "\n".join(result) + "\n"
        else:
            # Fallback if parsing fails
            return (
                indent
                + lines[0]
                + "\n"
                + "\n".join(indent + "  " + line.strip() for line in lines[1:])
                + "\n"
            )

    # Match var_name line and all following indented lines, capturing leading indent
    pattern = (
        rf"(?m)^(?P<indent>[ \t]*){re.escape(var_name)}:"  # indent + var_name:
        r"\s*!vault \|(?:\r?\n[ \t].*)*"  # vault block
    )
    new_content, count = re.subn(pattern, _repl, content)
    return new_content, count


def regen_vault_string(name, plaintext, vault_pass_file):
    """
    Encrypt a plaintext into an inline Ansible vault block.

    Args:
        name (str): The variable name
        plaintext (str): The plaintext to encrypt
        vault_pass_file (str): Path to the vault password file

    Returns:
        str: The encrypted vault block
    """
    # Use '--' to prevent plaintext starting with '-' being parsed as option
    cmd = [
        "ansible-vault",
        "encrypt_string",
        "--name",
        name,
        "--vault-password-file",
        vault_pass_file,
        "--",
        plaintext,
    ]
    proc = subprocess.run(cmd, check=True, stdout=subprocess.PIPE)
    return proc.stdout.decode()


def get_inline_vault_value(
    file_path, var_name, vault_password, verbose=False, strict=False
):
    """
    Get the decrypted value of an inline vault field from a file.

    Args:
        file_path (Path): Path to the file containing the vault field
        var_name (str): The variable name of the vault field
        vault_password (str): The vault password
        verbose (bool): Whether to print debug information
        strict (bool): If True, only try the provided password, not the fallback password

    Returns:
        str or None: The decrypted value, or None if not found or cannot be decrypted
    """
    try:
        # Read the file content
        content = file_path.read_text(encoding="utf-8")

        # Extract the vault block
        vault_block = extract_vault_block(content, var_name)
        if not vault_block:
            if verbose:
                print(f"Vault block not found for {var_name}")
            return None

        if verbose:
            print(f"Extracted vault block for {var_name}:\n{vault_block}")

        # First try using our verify_vault_password and create_vault_lib utilities
        if verify_vault_password(vault_block, vault_password, strict=strict):
            try:
                # Create vault lib and decrypt content
                vault_lib = create_vault_lib(vault_password, strict=strict)
                decrypted = vault_lib.decrypt(vault_block.encode()).decode("utf-8")
                if verbose:
                    print(f"Successfully decrypted {var_name} using VaultLib")
                return decrypted.strip()
            except Exception as e:
                if verbose:
                    print(f"VaultLib decryption failed: {e}")

        # If that failed, fall back to the subprocess approach for compatibility
        # Create a temporary file for the vault content
        with tempfile.NamedTemporaryFile(delete=False, mode="w") as vault_file:
            vault_file.write(vault_block)
            vault_file_path = vault_file.name

        # Create a temporary file for the password
        with tempfile.NamedTemporaryFile(delete=False, mode="w") as pwd_file:
            pwd_file.write(vault_password)
            pwd_file_path = pwd_file.name

        try:
            # Use ansible-vault directly for most reliable decryption
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

            if result.returncode == 0:
                decrypted = result.stdout.strip()
                if verbose:
                    print(f"Successfully decrypted {var_name}")
                return decrypted

            if verbose:
                print(f"ansible-vault view failed: {result.stderr}")

            # In strict mode, don't try any other methods
            if strict:
                return None

            # Only try fallback methods in non-strict mode
            # Try using decrypt instead of view as backup approach
            result = subprocess.run(
                [
                    "ansible-vault",
                    "decrypt",
                    "--vault-password-file",
                    pwd_file_path,
                    vault_file_path,
                    "--output",
                    "-",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode == 0:
                decrypted = result.stdout.strip()
                if verbose:
                    print(f"Successfully decrypted {var_name} with decrypt command")
                return decrypted

            if verbose:
                print(f"ansible-vault decrypt failed: {result.stderr}")

            return None
        finally:
            # Clean up temporary files
            try:
                if Path(vault_file_path).exists():
                    Path(vault_file_path).unlink()
                if Path(pwd_file_path).exists():
                    Path(pwd_file_path).unlink()
            except Exception as e:
                if verbose:
                    print(f"Failed to clean up temporary files: {e}")
    except Exception as e:
        if verbose:
            print(f"Error getting inline vault value: {e}")
        return None


def update_inline_vault_field(file_path, var_name, new_value, vault_password):
    """
    Update an inline vault field in a file.

    Args:
        file_path (Path): Path to the file containing the vault field
        var_name (str): The variable name of the vault field
        new_value (str): The new value to set
        vault_password (str): The vault password

    Returns:
        bool: True if the field was updated, False otherwise
    """
    try:
        # Read the file content
        content = file_path.read_text(encoding="utf-8")

        # Create a temporary file for the vault password
        with tempfile.NamedTemporaryFile(delete=False, mode="w") as tf:
            tf.write(vault_password)
            vault_file = tf.name

        # Generate the new vault block
        new_block = regen_vault_string(var_name, new_value, vault_file)

        # Replace the vault block
        new_content, count = replace_block(content, var_name, new_block)

        # Clean up the temporary file
        Path(vault_file).unlink()

        if count > 0:
            # Write the new content back to the file
            file_path.write_text(new_content, encoding="utf-8")
            return True

        return False
    except Exception as e:
        print(f"Error updating inline vault field: {e}")


def contains_vault_blocks(path):
    """
    Check if a file contains inline vault blocks.

    Args:
        path (Path): Path to the file to check

    Returns:
        bool: True if the file contains inline vault blocks, False otherwise
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        return "$ANSIBLE_VAULT" in text and "!vault" in text
    except Exception as e:
        logger.error(f"Error checking if file contains vault blocks: {e}")
        return False


def check_vault_blocks_with_password(path, password):
    """
    Check if all vault blocks in a file can be decrypted with the given password.

    Args:
        path (Path): Path to the file to check
        password (str): The vault password to check

    Returns:
        bool: True if all blocks can be decrypted, False otherwise
    """
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        # Regex to match vault blocks
        multiline_regex = re.compile(
            r"(?P<key>^\s*[^\s].*?:\s*)(?P<marker>!vault\s*\|)[\r\n]+"
            r"(?P<content>(?P<indent>\s*)(?:\$ANSIBLE_VAULT[^\r\n]*[\r\n]+)"
            r"(?:\s*[0-9A-Fa-f]+(?:[\r\n]+|$))+)",
            re.MULTILINE,
        )

        # Find all vault blocks
        for m in multiline_regex.finditer(text):
            raw = m.group("content")
            indent = m.group("indent")

            # Strip indent
            block = "".join(line[len(indent) :] for line in raw.splitlines(True))
            if not block.endswith("\n"):
                block += "\n"

            try:
                vault_secret = VaultSecret(password.encode())
                vault = VaultLib([(DEFAULT_VAULT_IDENTITY, vault_secret)])
                vault.decrypt(block.encode())
            except Exception:
                return False

        return True
    except Exception as e:
        logger.error(f"Error checking vault blocks with password: {e}")
        return False


def rotate_inline_blocks(text, old_password, new_password, dry_run=False):
    """
    Rotate the vault password for all inline vault blocks in text.

    Args:
        text (str): The text containing inline vault blocks
        old_password (str): The old vault password
        new_password (str): The new vault password
        dry_run (bool): If True, don't actually make changes

    Returns:
        str: The text with rotated vault blocks
        bool: True if changes were made, False otherwise
    """
    if not text.endswith("\n"):
        text += "\n"

    old_secret = VaultSecret(old_password.encode())
    new_secret = VaultSecret(new_password.encode())

    # Regex to match !vault | blocks including final line without newline
    multiline_regex = re.compile(
        r"(?P<key>^\s*[^\s].*?:\s*)(?P<marker>!vault\s*\|)[\r\n]+"
        r"(?P<content>(?P<indent>\s*)(?:\$ANSIBLE_VAULT[^\r\n]*[\r\n]+)"
        r"(?:\s*[0-9A-Fa-f]+(?:[\r\n]+|$))+)",
        re.MULTILINE,
    )

    modified = False

    def repl(m):
        nonlocal modified
        key = m.group("key")
        indent = m.group("indent")
        raw = m.group("content")

        # Strip indent
        block = "".join(line[len(indent) :] for line in raw.splitlines(True))
        if not block.endswith("\n"):
            block += "\n"

        try:
            # Write to temp file, rekey using VaultEditor
            with NamedTemporaryFile(mode="w+", delete=False) as tmp:
                tmp.write(block)
                tmp.flush()
                tmp_path = tmp.name

            # For dry-run, just verify we can decrypt but don't rekey
            if dry_run:
                try:
                    # Just check if we can decrypt with the old password
                    vault = VaultLib([(DEFAULT_VAULT_IDENTITY, old_secret)])
                    vault.decrypt(block.encode())
                    modified = True
                    # Return original in dry-run mode
                    return m.group(0)
                except Exception as e:
                    logger.error(f"Failed to decrypt vault block in dry-run: {e}")
                    return m.group(0)

            # Use VaultEditor to rekey the file directly
            VaultEditor(VaultLib([(DEFAULT_VAULT_IDENTITY, old_secret)])).rekey_file(
                tmp_path, new_secret
            )

            # Read the rekeyed content
            new_block_lines = Path(tmp_path).read_text().splitlines(True)
            Path(tmp_path).unlink()

            # Re-indent
            recoded = "".join(indent + line for line in new_block_lines)
            modified = True
            return f"{key}!vault |\n{recoded}"
        except Exception as e:
            logger.error(f"Failed to rotate vault block: {e}")
            # Return original if we can't decrypt/encrypt
            return m.group(0)

    new_text = multiline_regex.sub(repl, text)
    return new_text, modified
