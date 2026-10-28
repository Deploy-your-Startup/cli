"""Keep concurrent integration workers out of shared Ansible state."""

import pytest


@pytest.fixture(scope="session", autouse=True)
def isolated_ansible_home(tmp_path_factory):
    # Each xdist worker gets its own base directory. Galaxy installs and
    # temporary files must never race with another worker or touch the user home.
    home = tmp_path_factory.mktemp("ansible")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("ANSIBLE_HOME", str(home))
        patch.setenv("ANSIBLE_COLLECTIONS_PATH", str(home / "collections"))
        # The developer's own `startup config` (e.g. 1Password as vault
        # backend) must not change which backend a test talks to.
        patch.setenv("STARTUP_CONFIG_HOME", str(tmp_path_factory.mktemp("config")))
        for name in (
            "STARTUP_VAULT_BACKEND",
            "STARTUP_OP_VAULT",
            "OP_ACCOUNT",
            "STARTUP_BROWSER",
        ):
            patch.delenv(name, raising=False)
        yield home


def pytest_addoption(parser):
    parser.addoption(
        "--shard", help="Run one deterministic CI shard, e.g. 1/3 (default: all tests)"
    )


def pytest_collection_modifyitems(config, items):
    shard = config.getoption("--shard")
    if shard is None:
        return
    try:
        index, total = map(int, shard.split("/"))
        if not 1 <= index <= total:
            raise ValueError
    except ValueError as error:
        raise pytest.UsageError(
            "--shard must be INDEX/TOTAL with 1 <= INDEX <= TOTAL"
        ) from error
    # Every runner collects the same suite. Sorting node IDs makes assignment
    # independent of collection order; parametrized cases can run separately.
    selected, deselected = [], []
    for position, item in enumerate(sorted(items, key=lambda item: item.nodeid)):
        (selected if position % total == index - 1 else deselected).append(item)
    items[:] = selected
    config.hook.pytest_deselected(items=deselected)
