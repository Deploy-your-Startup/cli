"""Keep real Ansible collection installs private to each pytest worker."""

import pytest


@pytest.fixture(scope="session", autouse=True)
def isolated_ansible_collections(tmp_path_factory):
    # GIVEN parallel CLI processes install real collections, each worker needs
    # its own destination so another worker cannot remove a collection in use.
    collections = tmp_path_factory.mktemp("ansible-collections")
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("ANSIBLE_COLLECTIONS_PATH", str(collections))
        yield
