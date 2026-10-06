"""Inline parsing requirements exercised through the real CLI and Ansible."""

import pytest

from tests.test_vault_process_integration import cli, decrypt_field, inline


@pytest.mark.parametrize("indent", ["", "  ", "          "])
@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_update_preserves_surrounding_yaml_and_normalizes_body(
    tmp_path, indent, newline
):
    # GIVEN adjacent real blocks, a heading comment and no final newline.
    path = tmp_path / "vars.yml"
    prefix = "parent:\n" if indent else ""
    block = inline(tmp_path, indent=indent).replace(
        "!vault |", "!vault | # secret comment"
    )
    neighbour = inline(tmp_path, name="secret_two", indent=indent)
    suffix = f"{indent}# after blocks\n{indent}plain: unchanged"
    original = (prefix + block + neighbour + suffix).replace("\n", newline)
    path.write_bytes(original.encode())
    # WHEN only the exact field name is replaced.
    result = cli(
        tmp_path, "update", "-r", path, "--field-stdin", "secret", stdin=b"replacement"
    )
    # THEN the heading comment and every byte outside the block survive.
    assert result.returncode == 0, result.stdout + result.stderr
    text = path.read_bytes().decode()
    assert text.startswith(
        (prefix + indent + "secret: !vault | # secret comment\n").replace("\n", newline)
    )
    assert text.endswith((neighbour + suffix).replace("\n", newline))
    assert indent + "  $ANSIBLE_VAULT" in text
    assert decrypt_field(tmp_path, path).stdout == b"replacement"
    assert decrypt_field(tmp_path, path, "secret_two").stdout == b"old"


def test_blank_line_inside_ciphertext_and_no_final_newline(tmp_path):
    # GIVEN a real block with an empty line inside its ciphertext.
    path = tmp_path / "vars.yml"
    lines = inline(tmp_path).splitlines()
    lines.insert(3, "")
    path.write_text("\n".join(lines))
    # WHEN it is read and replaced through the CLI.
    read = cli(tmp_path, "get-field", "-f", path, "--field", "secret")
    assert read.returncode == 0, read.stderr
    assert read.stdout == b"old\n"
    replaced = cli(
        tmp_path, "update", "-r", path, "--field-stdin", "secret", stdin=b"new"
    )
    # THEN the whole scalar is replaced and the absent terminal newline stays absent.
    assert replaced.returncode == 0, replaced.stdout + replaced.stderr
    assert not path.read_bytes().endswith(b"\n")
    assert decrypt_field(tmp_path, path).stdout == b"new"


@pytest.mark.parametrize(
    "body", ["", "  $ANSIBLE_VAULT;1.1;AES256\n", "  012345abcdef\n"]
)
def test_malformed_blocks_fail_without_writing(tmp_path, body):
    # GIVEN an incomplete or headerless Vault scalar.
    path = tmp_path / "vars.yml"
    path.write_text("secret: !vault |\n" + body + "other: keep\n")
    original = path.read_bytes()
    # WHEN a user attempts to read or replace it.
    read = cli(tmp_path, "get-field", "-f", path, "--field", "secret")
    write = cli(tmp_path, "update", "-r", path, "--field-stdin", "secret", stdin=b"new")
    # THEN neither reports success or changes the malformed file.
    assert read.returncode != 0
    assert write.returncode != 0
    assert path.read_bytes() == original
