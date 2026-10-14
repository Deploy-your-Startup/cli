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
        yield home
