import click
import pytest

from cli import ansible_commands, tailnet


def _status(*names, state="Running", tailnet_name="philipp-lein.github"):
    return {
        "BackendState": state,
        "CurrentTailnet": {"Name": tailnet_name},
        "Self": {"DNSName": "laptop.tail1234.ts.net."},
        "Peer": {
            f"key-{name}": {"DNSName": f"{name}.tail1234.ts.net."} for name in names
        },
    }


@pytest.fixture
def local_tailscale(monkeypatch):
    """Stand in for this machine's Tailscale: set .binary and .status per test."""

    class Local:
        binary = "/usr/bin/tailscale"
        status = None

    monkeypatch.setattr(tailnet, "tailscale_binary", lambda: Local.binary)
    monkeypatch.setattr(tailnet, "read_status", lambda _: Local.status)
    return Local


def test_network_mode_defaults_to_public(tmp_path):
    assert tailnet.resolve_network_mode(tmp_path, "production") == "public"


def test_environment_file_overrides_all_yml(tmp_path):
    group_vars = tmp_path / "group_vars"
    group_vars.mkdir()
    (group_vars / "all.yml").write_text("network_mode: public\n")
    (group_vars / "production.yml").write_text(
        'network_mode: "private"  # tailnet only\nnested:\n  network_mode: public\n'
    )

    assert tailnet.resolve_network_mode(tmp_path, "production") == "private"
    assert tailnet.resolve_network_mode(tmp_path, "staging") == "public"


def test_private_hosts_are_read_from_the_hetzner_label():
    hostvars = {
        "my-shop-master-0": {"hcloud_labels": {"type": "master", "network": "private"}},
        "other-master-0": {"hcloud_labels": {"type": "master"}},
        "byos": {},
    }

    assert tailnet.private_hosts(hostvars) == ["my-shop-master-0"]


def test_private_hosts_accept_labels_as_ansible_inventory_prints_them():
    # Verbatim shape of `ansible-inventory --list` for a private Hetzner server.
    hostvars = {
        "my-shop-master-0": {
            "hcloud_labels": {
                "ingress": {"__ansible_unsafe": "true"},
                "network": {"__ansible_unsafe": "private"},
                "type": {"__ansible_unsafe": "master"},
            }
        },
        "other-master-0": {"hcloud_labels": {"type": {"__ansible_unsafe": "master"}}},
    }

    assert tailnet.private_hosts(hostvars) == ["my-shop-master-0"]


def test_missing_tailscale_explains_how_to_install(local_tailscale):
    local_tailscale.binary = None

    with pytest.raises(click.ClickException, match="not installed") as error:
        tailnet.ensure_tailnet_access("my-shop", ["my-shop-master-0"])

    assert "tailscale.com/download" in error.value.message


def test_stopped_daemon_is_reported(local_tailscale):
    with pytest.raises(click.ClickException, match="daemon is not running"):
        tailnet.ensure_tailnet_access("my-shop", ["my-shop-master-0"])


def test_logged_out_client_is_reported(local_tailscale):
    local_tailscale.status = _status(state="NeedsLogin")

    with pytest.raises(click.ClickException, match="state: NeedsLogin"):
        tailnet.ensure_tailnet_access("my-shop", ["my-shop-master-0"])


def test_reachable_hosts_pass(local_tailscale):
    local_tailscale.status = _status("my-shop-master-0")

    tailnet.ensure_tailnet_access("my-shop", ["my-shop-master-0"])


def test_host_outside_the_tailnet_names_the_tailnet(local_tailscale):
    local_tailscale.status = _status("somebody-else")

    with pytest.raises(click.ClickException, match=r"philipp-lein\.github"):
        tailnet.ensure_tailnet_access("my-shop", ["my-shop-master-0"])


def test_non_strict_only_warns_about_missing_hosts(local_tailscale, capsys):
    local_tailscale.status = _status()

    tailnet.ensure_tailnet_access("my-shop", ["my-shop-master-0"], strict=False)

    assert "Not found in" in capsys.readouterr().err


def test_public_project_never_queries_the_inventory(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("inventory queried for a public project")

    monkeypatch.setattr(ansible_commands, "_dynamic_inventory_hostvars", fail)

    ansible_commands._ensure_tailnet(tmp_path, "production", ".shared-roles", {})
