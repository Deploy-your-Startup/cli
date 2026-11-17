"""Keep concurrent integration workers out of shared user state."""

import os
import shlex

import pytest


@pytest.fixture(scope="session", autouse=True)
def reject_ambient_startup(tmp_path_factory):
    """Catch accidental CLI calls even if the caller ignores their exit status."""
    binaries = tmp_path_factory.mktemp("ambient-startup")
    calls = binaries / "calls"
    startup = binaries / "startup"
    startup.write_text(
        f"#!/bin/sh\nprintf 'unexpected startup call\\n' >> {shlex.quote(str(calls))}\n"
        "echo 'Use the checkout Python with -m cli.startup' >&2\nexit 97\n"
    )
    startup.chmod(0o755)
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("PATH", str(binaries) + os.pathsep + os.environ["PATH"])
        yield
    assert not calls.exists(), "Tests called startup through the ambient PATH"


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
        for name in ("STARTUP_VAULT_BACKEND", "STARTUP_OP_VAULT", "OP_ACCOUNT"):
            patch.delenv(name, raising=False)
        yield home


@pytest.fixture(scope="session", autouse=True)
def isolated_copier_cache(tmp_path_factory):
    # Concurrent mirror refreshes can race when a remote advances during tests.
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("COPIER_CACHE_DIR", str(tmp_path_factory.mktemp("copier-cache")))
        yield


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
