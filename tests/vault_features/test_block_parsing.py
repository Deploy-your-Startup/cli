"""What counts as a vault block, and what a rewritten one looks like.

These pin the behaviour that `update_vault_secrets` and `cli.vault.fields` used
to implement twice, with quietly different answers. The two agreed on every
well-formed file and disagreed only on malformed ones - which is the worst
place to have two opinions, because that is where you need one you can name.

The surviving answer is the stricter one: a block is a `$ANSIBLE_VAULT` header
followed by at least one line of ciphertext. Anything else is not a block, and
saying so beats handing back a fragment that only fails later.
"""

from cli.update_vault_secrets import update_secrets
from cli.vault.fields import extract_vault_block, normalize_vault_block

HEX = "3061626364" * 4
PASSWORD = "test-vault-password"


def block(*lines, indent="  ", name="secret"):
    body = "".join(f"{indent}{line}\n" for line in lines)
    return f"{name}: !vault |\n{body}"


def test_a_well_formed_block_comes_back_without_its_indentation():
    # GIVEN a normal field between two plain ones
    content = "a: 1\n" + block("$ANSIBLE_VAULT;1.1;AES256", HEX, HEX) + "b: 2\n"

    # THEN only the block's own lines come back, unindented
    assert extract_vault_block(content, "secret") == f"$ANSIBLE_VAULT;1.1;AES256\n{HEX}\n{HEX}"


def test_the_next_field_does_not_bleed_into_the_block():
    # GIVEN two vault fields in a row
    content = block("$ANSIBLE_VAULT;1.1;AES256", HEX) + block(
        "$ANSIBLE_VAULT;1.1;AES256", HEX, name="other"
    )

    # THEN the first stops at its own last line
    assert extract_vault_block(content, "secret") == f"$ANSIBLE_VAULT;1.1;AES256\n{HEX}"


def test_indentation_depth_does_not_matter():
    # GIVEN the deep indentation `ansible-vault encrypt_string` produces
    content = block("$ANSIBLE_VAULT;1.1;AES256", HEX, indent=" " * 10) + "b: 2\n"

    # THEN it reads the same as a two-space block
    assert extract_vault_block(content, "secret") == f"$ANSIBLE_VAULT;1.1;AES256\n{HEX}"


def test_a_nested_field_is_found_too():
    # GIVEN a block under a parent key
    content = "parent:\n  secret: !vault |\n    $ANSIBLE_VAULT;1.1;AES256\n    " + HEX + "\n"

    # THEN
    assert extract_vault_block(content, "secret") == f"$ANSIBLE_VAULT;1.1;AES256\n{HEX}"


def test_a_blank_line_inside_the_block_does_not_end_it():
    # GIVEN a stray blank line, which hand-editing leaves behind
    content = block("$ANSIBLE_VAULT;1.1;AES256", "", HEX) + "b: 2\n"

    # THEN the ciphertext after it is still part of the block. The other
    # implementation stopped at the blank line and returned a header alone.
    assert extract_vault_block(content, "secret") == f"$ANSIBLE_VAULT;1.1;AES256\n{HEX}"


def test_ciphertext_without_a_header_is_not_a_block():
    # GIVEN lines that look like ciphertext but carry no vault header
    content = block(HEX, HEX)

    # THEN this is not a vault block. Handing the bytes back would only move
    # the failure to the decryption step, with a worse message.
    assert extract_vault_block(content, "secret") is None


def test_a_header_with_no_ciphertext_is_not_a_block():
    # GIVEN a truncated field
    content = block("$ANSIBLE_VAULT;1.1;AES256")

    # THEN
    assert extract_vault_block(content, "secret") is None


def test_a_missing_field_is_none():
    assert extract_vault_block("a: 1\nb: 2\n", "secret") is None


def test_a_field_whose_name_is_a_prefix_is_not_matched(tmp_path):
    # GIVEN only `secret_two`, while `secret` is asked for
    content = block("$ANSIBLE_VAULT;1.1;AES256", HEX, name="secret_two")

    # THEN
    assert extract_vault_block(content, "secret") is None


def test_normalize_reindents_whatever_it_is_given():
    # GIVEN the deep indentation ansible-vault emits
    raw = "secret: !vault |\n          $ANSIBLE_VAULT;1.1;AES256\n          " + HEX + "\n"

    # THEN content lines sit two spaces in, and the head keeps the caller's
    assert normalize_vault_block(raw) == f"secret: !vault |\n  $ANSIBLE_VAULT;1.1;AES256\n  {HEX}\n"
    assert normalize_vault_block(raw, "    ").startswith("    secret: !vault |\n      $")


def test_a_rewritten_field_stays_readable_and_keeps_its_neighbours(tmp_path):
    # GIVEN a file with a plain value on either side of the secret
    path = tmp_path / "all.yml"
    path.write_text("before: 1\nafter: 2\n")
    update_secrets(
        repo=str(path),
        vault_password=PASSWORD,
        set_field=[("secret", "first")],
        create_in=str(path),
    )

    # WHEN the field is replaced
    update_secrets(
        repo=str(path), vault_password=PASSWORD, set_field=[("secret", "second")]
    )

    # THEN the block is still one readable block and the plain values survived
    text = path.read_text()
    assert text.count("secret: !vault |") == 1
    assert "before: 1" in text and "after: 2" in text
    assert extract_vault_block(text, "secret").startswith("$ANSIBLE_VAULT;")
