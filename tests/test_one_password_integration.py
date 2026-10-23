"""GIVEN 1Password as the vault password backend, WHEN the CLI stores or reads a
project's vault password, THEN it goes through `op` on stdin/stdout only and
the per-computer setting decides which backend is used.

1Password is replaced at its boundary: a fake `op` executable keeps vaults and
items in a JSON file and records every argument list it receives. Ansible, the
CLI and the user config file are real.
"""

import json
import subprocess
import sys
import textwrap

from tests.test_vault_handoff_integration import (
    ENTRY,
    PASSWORD,
    SECRET,
    keychain_stand_in,
    run_in_terminal,
    vaulted_project,
)

FAKE_OP = textwrap.dedent(
    """\
    import json, os, sys, uuid
    from pathlib import Path

    store_path = Path(os.environ["STARTUP_TEST_OP_STORE"])
    with open(os.environ["STARTUP_TEST_OP_CALLS"], "a") as calls:
        calls.write(json.dumps(sys.argv[1:]) + "\\n")
    store = json.loads(store_path.read_text())
    args = [a for a in sys.argv[1:] if a not in ("--format", "json", "--no-newline")]
    if "--account" in args:
        index = args.index("--account")
        del args[index:index + 2]


    def fail(message):
        sys.stderr.write("[ERROR] " + message + "\\n")
        sys.exit(1)


    def option(name):
        return args[args.index(name) + 1]


    def vault(name):
        if name not in store["vaults"]:
            fail(f'"{name}" isn\\'t a vault in this account.')
        return store["vaults"][name]


    if not store["signed_in"]:
        if args[:2] == ["account", "list"]:
            print("[]")
            sys.exit(0)
        fail("account is not signed in")
    if args[:2] == ["account", "list"]:
        print(json.dumps([{"email": "synthetic@example.com"}]))
    elif args[:2] == ["vault", "get"]:
        vault(args[2])
        print(json.dumps({"name": args[2]}))
    elif args[0] == "read":
        name, title, field = args[1].removeprefix("op://").split("/")
        items = [i for i in vault(name).values() if i["title"] == title]
        if not items:
            fail(f'"{title}" isn\\'t an item in the "{name}" vault.')
        value = next(f["value"] for f in items[0]["fields"] if f["id"] == field)
        sys.stdout.write(value)
    elif args[:2] == ["item", "list"]:
        items = vault(option("--vault"))
        print(json.dumps([{"id": k, "title": v["title"]} for k, v in items.items()]))
    elif args[:2] == ["item", "create"]:
        assert args[-1] == "-", args
        item = json.loads(sys.stdin.read())
        item_id = uuid.uuid4().hex
        vault(option("--vault"))[item_id] = item
        print(json.dumps({"id": item_id}))
    elif args[:2] == ["item", "get"]:
        print(json.dumps(vault(option("--vault"))[args[2]]))
    elif args[:2] == ["item", "edit"]:
        vault(option("--vault"))[args[2]] = json.loads(sys.stdin.read())
        print(json.dumps({"id": args[2]}))
    else:
        fail("unsupported: " + " ".join(args))
    store_path.write_text(json.dumps(store))
    """
)


def one_password_stand_in(tmp_path, *, vaults=("Startups",), signed_in=True):
    """Return (env, store) with a fake `op`, a Keychain stand-in and own config."""
    tmp_path.mkdir(exist_ok=True)
    env, _ = keychain_stand_in(tmp_path)
    bin_dir = tmp_path / "bin"
    script = tmp_path / "fake_op.py"
    script.write_text(FAKE_OP)
    op = bin_dir / "op"
    op.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n')
    op.chmod(0o755)
    store = tmp_path / "op-store.json"
    store.write_text(
        json.dumps({"signed_in": signed_in, "vaults": {name: {} for name in vaults}})
    )
    env.update(
        STARTUP_TEST_OP_STORE=str(store),
        STARTUP_TEST_OP_CALLS=str(tmp_path / "op-calls"),
        STARTUP_CONFIG_HOME=str(tmp_path / "config"),
    )
    for name in ("STARTUP_VAULT_BACKEND", "STARTUP_OP_VAULT", "OP_ACCOUNT"):
        env.pop(name, None)
    return env, store


def startup(env, cwd, *args, stdin=subprocess.DEVNULL):
    return subprocess.run(
        [sys.executable, "-m", "cli.startup", *args],
        env=env,
        cwd=cwd,
        stdin=stdin,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def items(store, vault="Startups"):
    return list(json.loads(store.read_text())["vaults"][vault].values())


def password_of(item):
    return next(f["value"] for f in item["fields"] if f["id"] == "password")


def op_arguments(tmp_path):
    calls = tmp_path / "op-calls"
    return calls.read_text() if calls.exists() else ""


def store_item(store, title, value, vault="Startups"):
    data = json.loads(store.read_text())
    data["vaults"][vault]["existing"] = {
        "title": title,
        "category": "PASSWORD",
        "fields": [
            {"id": "password", "type": "CONCEALED", "value": value},
            {"id": "username", "type": "STRING", "value": "kept-by-user"},
        ],
    }
    store.write_text(json.dumps(data))


def test_configured_one_password_serves_the_vault_password(tmp_path):
    # GIVEN a project vault and its password in the 1Password vault "Startups".
    env, store = one_password_stand_in(tmp_path)
    vault = vaulted_project(tmp_path)
    store_item(store, ENTRY, PASSWORD)

    # WHEN the user picks 1Password once for this computer.
    chosen = startup(env, tmp_path, "config", "set", "vault-backend", "1password")
    assert chosen.returncode == 0, chosen.stderr + chosen.stdout

    # THEN the choice lands in the user config, `config show` reports it, and a
    # command without --vault-password reads the vault through 1Password.
    config = (tmp_path / "config" / "config.toml").read_text()
    assert 'backend = "1password"' in config
    assert 'op_vault = "Startups"' in config
    shown = startup(env, tmp_path, "config", "show")
    assert "1password" in shown.stdout
    assert "Startups" in shown.stdout
    result = startup(env, tmp_path, "secrets", "get-file", "-f", str(vault))
    assert result.returncode == 0, result.stderr
    assert result.stdout == SECRET.decode()
    assert PASSWORD not in op_arguments(tmp_path)


def test_environment_overrides_the_saved_backend(tmp_path):
    # GIVEN 1Password saved as backend but holding no item for the project.
    env, _ = one_password_stand_in(tmp_path)
    vault = vaulted_project(tmp_path)
    assert (
        startup(env, tmp_path, "config", "set", "vault-backend", "1password").returncode
        == 0
    )
    (tmp_path / "keychain" / ENTRY).write_text(PASSWORD)

    # WHEN one run asks for the Keychain through STARTUP_VAULT_BACKEND.
    result = startup(
        {**env, "STARTUP_VAULT_BACKEND": "keychain"},
        tmp_path,
        "secrets",
        "get-file",
        "-f",
        str(vault),
    )

    # THEN that run uses the Keychain and never calls `op` for the password.
    assert result.returncode == 0, result.stderr
    assert result.stdout == SECRET.decode()
    assert '"read"' not in op_arguments(tmp_path)


def test_config_rejects_a_vault_op_cannot_open(tmp_path):
    # GIVEN 1Password without a vault named "Missing", and a signed-out `op`.
    env, _ = one_password_stand_in(tmp_path)
    signed_out_env, _ = one_password_stand_in(tmp_path / "out", signed_in=False)

    # WHEN the user configures either.
    missing = startup(
        env,
        tmp_path,
        "config",
        "set",
        "vault-backend",
        "1password",
        "--op-vault",
        "Missing",
    )
    signed_out = startup(
        signed_out_env, tmp_path, "config", "set", "vault-backend", "1password"
    )

    # THEN nothing is saved and each error says what to fix.
    assert missing.returncode != 0
    assert 'no vault "Missing"' in missing.stderr
    assert signed_out.returncode != 0
    assert "Integrate with 1Password CLI" in signed_out.stderr
    assert not (tmp_path / "config" / "config.toml").exists()
    assert not (tmp_path / "out" / "config" / "config.toml").exists()


def test_signed_out_op_fails_without_prompting(tmp_path):
    # GIVEN 1Password as backend, but the app integration is locked.
    env, store = one_password_stand_in(tmp_path)
    vault = vaulted_project(tmp_path)
    assert (
        startup(env, tmp_path, "config", "set", "vault-backend", "1password").returncode
        == 0
    )
    data = json.loads(store.read_text())
    data["signed_in"] = False
    store.write_text(json.dumps(data))

    # WHEN a command needs the password.
    result = startup(env, tmp_path, "secrets", "get-file", "-f", str(vault))

    # THEN it explains how to unlock 1Password and prints no secret.
    assert result.returncode != 0
    assert "Integrate with 1Password CLI" in result.stderr
    assert result.stdout == ""


def test_keychain_password_is_copied_to_one_password(tmp_path):
    # GIVEN the project's password in the Keychain and 1Password configured.
    env, store = one_password_stand_in(tmp_path)
    vaulted_project(tmp_path)
    assert (
        startup(env, tmp_path, "config", "set", "vault-backend", "1password").returncode
        == 0
    )
    (tmp_path / "keychain" / ENTRY).write_text(PASSWORD)

    # WHEN the user copies it, twice.
    project = tmp_path / "demo-app"
    first = startup(
        env,
        tmp_path,
        "secrets",
        "copy-password",
        "-r",
        str(project),
        "--from",
        "keychain",
    )
    again = startup(
        env,
        tmp_path,
        "secrets",
        "copy-password",
        "-r",
        str(project),
        "--from",
        "keychain",
    )

    # THEN one Password item holds it, the Keychain keeps its entry, and the
    # password never appeared in `op` arguments or the output.
    assert first.returncode == 0, first.stderr
    assert again.returncode == 0, again.stderr
    assert ENTRY in first.stdout
    [item] = items(store)
    assert item["title"] == ENTRY
    assert item["category"] == "PASSWORD"
    assert password_of(item) == PASSWORD
    assert (tmp_path / "keychain" / ENTRY).read_text() == PASSWORD
    assert PASSWORD not in op_arguments(tmp_path)
    assert PASSWORD not in first.stdout + first.stderr


def test_copy_updates_a_stale_item_and_refuses_a_wrong_password(tmp_path):
    # GIVEN a stale 1Password item with a field the user added.
    env, store = one_password_stand_in(tmp_path)
    vaulted_project(tmp_path)
    assert (
        startup(env, tmp_path, "config", "set", "vault-backend", "1password").returncode
        == 0
    )
    store_item(store, ENTRY, "stale-synthetic-password")
    project = tmp_path / "demo-app"

    # WHEN the Keychain first holds a wrong password, then the right one.
    (tmp_path / "keychain" / ENTRY).write_text("wrong-synthetic-password")
    wrong = startup(
        env,
        tmp_path,
        "secrets",
        "copy-password",
        "-r",
        str(project),
        "--from",
        "keychain",
    )
    unchanged = password_of(items(store)[0])
    (tmp_path / "keychain" / ENTRY).write_text(PASSWORD)
    right = startup(
        env,
        tmp_path,
        "secrets",
        "copy-password",
        "-r",
        str(project),
        "--from",
        "keychain",
    )

    # THEN the wrong one is refused without touching the item, and the right
    # one replaces only the password of the same item.
    assert wrong.returncode != 0
    assert "Nothing was copied" in wrong.stderr
    assert unchanged == "stale-synthetic-password"
    assert right.returncode == 0, right.stderr
    [item] = items(store)
    assert password_of(item) == PASSWORD
    assert {"id": "username", "type": "STRING", "value": "kept-by-user"} in item[
        "fields"
    ]
    assert PASSWORD not in op_arguments(tmp_path)


def test_missing_item_is_recovered_into_one_password(tmp_path):
    # GIVEN 1Password configured but without an item for the project.
    env, store = one_password_stand_in(tmp_path)
    vault = vaulted_project(tmp_path)
    assert (
        startup(env, tmp_path, "config", "set", "vault-backend", "1password").returncode
        == 0
    )

    # WHEN a command runs in a terminal and the user pastes the password.
    code, text = run_in_terminal(
        [sys.executable, "-m", "cli.startup", "secrets", "get-file", "-f", str(vault)],
        env,
        tmp_path,
        [(b"Vault password", PASSWORD.encode(), 1)],
    )

    # THEN the vault opens and the password is stored as a 1Password item.
    assert code == 0, text
    assert 'in 1Password (vault "Startups")' in text
    assert SECRET.decode().strip() in text
    assert PASSWORD not in text
    assert [password_of(item) for item in items(store)] == [PASSWORD]
    assert PASSWORD not in op_arguments(tmp_path)


def test_bootstrap_offers_one_password_once(tmp_path):
    # GIVEN a signed-in `op`, no saved choice and a 1Password vault "Startups".
    env, _ = one_password_stand_in(tmp_path)
    offer = (
        "from cli.user_config_commands import offer_one_password; "
        "from cli.vault_handoff import hand_off_to_password_manager; "
        "offer_one_password(); "
        f"hand_off_to_password_manager('demo-app', {PASSWORD!r})"
    )

    # WHEN the user accepts, first names a vault that does not exist, then
    # the default one.
    code, text = run_in_terminal(
        [sys.executable, "-c", offer],
        env,
        tmp_path,
        [
            (b"Keep vault passwords in 1Password", b"y", 1),
            (b"1Password vault", b"Missing", 1),
            (b"Try another vault?", b"y", 1),
            (b"1Password vault", b"", 2),
        ],
    )

    # THEN the choice is saved and the clipboard hand-off is skipped, because
    # 1Password already carries the password to other computers.
    assert code == 0, text
    assert 'no vault "Missing"' in text
    config = (tmp_path / "config" / "config.toml").read_text()
    assert 'backend = "1password"' in config
    assert 'op_vault = "Startups"' in config
    assert "Saved it as" not in text
    assert not (tmp_path / "clipboard").exists()

    # AND a second bootstrap does not ask again.
    code, text = run_in_terminal(
        [
            sys.executable,
            "-c",
            "from cli.user_config_commands import offer_one_password; offer_one_password()",
        ],
        env,
        tmp_path,
        [],
    )
    assert code == 0, text
    assert "Keep vault passwords" not in text


def test_declining_one_password_keeps_the_keychain(tmp_path):
    # GIVEN a signed-in `op` and no saved choice.
    env, _ = one_password_stand_in(tmp_path)

    # WHEN the user declines the offer.
    code, text = run_in_terminal(
        [
            sys.executable,
            "-c",
            "from cli.user_config_commands import offer_one_password; offer_one_password()",
        ],
        env,
        tmp_path,
        [(b"Keep vault passwords in 1Password", b"n", 1)],
    )

    # THEN the Keychain is saved as the choice, so the question is not repeated.
    assert code == 0, text
    config = (tmp_path / "config" / "config.toml").read_text()
    assert 'backend = "keychain"' in config


def test_doctor_reports_the_one_password_vault(tmp_path):
    # GIVEN 1Password configured, then its vault removed.
    env, store = one_password_stand_in(tmp_path)
    assert (
        startup(env, tmp_path, "config", "set", "vault-backend", "1password").returncode
        == 0
    )

    # WHEN doctor runs before and after.
    healthy = startup(env, tmp_path, "doctor")
    store.write_text(json.dumps({"signed_in": True, "vaults": {}}))
    broken = startup(env, tmp_path, "doctor")

    # THEN it names the vault, and the missing vault is a failed check.
    assert 'Vault passwords in 1Password (vault "Startups")' in healthy.stdout
    assert (
        'Missing: Vault passwords in 1Password: 1Password has no vault "Startups"'
        in broken.stdout
    )
    assert broken.returncode != 0
