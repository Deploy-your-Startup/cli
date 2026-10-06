"""Read and replace inline Vault scalars without reserializing surrounding YAML."""

import logging
import re
import sys
from dataclasses import dataclass

from .process import VaultError, decrypt, encrypt_inline, rekey

logger = logging.getLogger(__name__)


@dataclass
class InlineBlock:
    name: str
    start: int
    end: int
    heading: str
    indent: str
    ciphertext: str
    newline: str
    terminated: bool


def _blocks(content):
    # Limit the body to indented Vault header/hex lines. A broad "all indented
    # lines" match consumes neighbouring nested fields and comments.
    lines = content.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    heading = re.compile(
        r"^(?P<indent>[ \t]*)(?P<name>[^:#\s]+):[ \t]*!vault[ \t]+\|[ \t]*(?:#[^\r\n]*)?(?:\r?\n|$)"
    )
    i = 0
    while i < len(lines):
        match = heading.fullmatch(lines[i])
        if not match:
            i += 1
            continue
        indent = match.group("indent")
        body = []
        j = i + 1
        last = j
        while j < len(lines):
            raw = lines[j]
            text = raw.strip()
            if not text:
                j += 1
                continue
            depth = len(raw) - len(raw.lstrip(" \t"))
            if depth <= len(indent):
                break
            valid = (
                text.startswith("$ANSIBLE_VAULT;")
                if not body
                else bool(re.fullmatch(r"[0-9a-fA-F]+", text))
            )
            if not valid:
                break
            body.append(text)
            j += 1
            last = j
        if len(body) < 2:
            raise VaultError("Malformed inline Vault block.")
        yield InlineBlock(
            match.group("name"),
            offsets[i],
            offsets[last],
            lines[i],
            indent,
            "\n".join(body) + "\n",
            "\r\n" if lines[i].endswith("\r\n") else "\n",
            lines[last - 1].endswith("\n"),
        )
        i = last


def extract_vault_block(content, var_name):
    try:
        for block in _blocks(content):
            if block.name == var_name:
                return block.ciphertext.rstrip("\n")
    except VaultError:
        return None
    return None


def normalize_vault_block(block, indent=""):
    lines = block.rstrip("\n").split("\n")
    name, _, marker = lines[0].partition(":")
    head = f"{indent}{name}: {marker.strip()}" if marker else indent + lines[0]
    return (
        "\n".join([head, *[indent + "  " + line.strip() for line in lines[1:]]]) + "\n"
    )


def _render(block, ciphertext):
    # Preserve the original heading (including a comment) and surrounding bytes.
    body = block.newline.join(
        block.indent + "  " + line.strip() for line in ciphertext.splitlines()
    )
    return block.heading + body + (block.newline if block.terminated else "")


def replace_block(content, var_name, new_block):
    replacements = [block for block in _blocks(content) if block.name == var_name]
    ciphertext = "\n".join(new_block.splitlines()[1:])
    for block in reversed(replacements):
        content = (
            content[: block.start] + _render(block, ciphertext) + content[block.end :]
        )
    return content, len(replacements)


def verify_inline_field(content, var_name, password):
    """Verify every matching scalar before replacing duplicate field names."""
    try:
        matches = [block for block in _blocks(content) if block.name == var_name]
        for block in matches:
            decrypt(block.ciphertext, password)
        return bool(matches)
    except VaultError:
        return False


def regen_vault_string(name, plaintext, vault_password):
    return encrypt_inline(name, plaintext, vault_password)


def get_inline_vault_value(
    file_path, var_name, vault_password, verbose=False, strict=False
):
    try:
        block = extract_vault_block(file_path.read_bytes().decode(), var_name)
        if block is None:
            return None
        return decrypt(block, vault_password).decode()
    except (OSError, UnicodeError, VaultError):
        if verbose:
            print(
                "Could not decrypt the requested inline Vault field.", file=sys.stderr
            )
        return None


def update_inline_vault_field(file_path, var_name, new_value, vault_password):
    try:
        content = file_path.read_bytes().decode()
        new_block = regen_vault_string(var_name, new_value, vault_password)
        new_content, count = replace_block(content, var_name, new_block)
        if count:
            file_path.write_bytes(new_content.encode())
            return True
        return False
    except (OSError, UnicodeError, VaultError):
        print("Could not update the inline Vault field.", file=sys.stderr)
        return False


def contains_vault_blocks(path):
    try:
        return "!vault" in path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False


def check_vault_blocks_with_password(path, password):
    try:
        blocks = list(_blocks(path.read_bytes().decode()))
        if not blocks:
            return False
        for block in blocks:
            decrypt(block.ciphertext, password)
        return True
    except (OSError, UnicodeError, VaultError):
        return False


def rotate_inline_blocks(text, old_password, new_password, dry_run=False):
    # Prepare every replacement before returning any changed content. A later
    # failure must not cause a partially rotated file to be written.
    blocks = list(_blocks(text))
    replacements = []
    for block in blocks:
        if dry_run:
            decrypt(block.ciphertext, old_password)
        else:
            replacements.append(
                (block, rekey(block.ciphertext, old_password, new_password).decode())
            )
    for block, ciphertext in reversed(replacements):
        text = text[: block.start] + _render(block, ciphertext) + text[block.end :]
    return text, bool(blocks)
