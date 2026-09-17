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


def _check(hosts, *, binary="/usr/bin/tailscale", status=None, strict=True):
    tailnet.ensure_tailnet_access(
        "my-shop",
        hosts,
        strict=strict,
        binary_finder=lambda: binary,
        status_reader=lambda _: status,
    )


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


def test_missing_tailscale_explains_how_to_install():
    with pytest.raises(click.ClickException, match="not installed") as error:
        _check(["my-shop-master-0"], binary=None)

    assert "tailscale.com/download" in error.value.message


def test_stopped_daemon_is_reported():
    with pytest.raises(click.ClickException, match="daemon is not running"):
        _check(["my-shop-master-0"], status=None)


def test_logged_out_client_is_reported():
    with pytest.raises(click.ClickException, match="state: NeedsLogin"):
        _check(["my-shop-master-0"], status=_status(state="NeedsLogin"))


def test_reachable_hosts_pass():
    _check(["my-shop-master-0"], status=_status("my-shop-master-0"))


def test_host_outside_the_tailnet_names_the_tailnet():
    with pytest.raises(click.ClickException, match=r"philipp-lein\.github"):
        _check(["my-shop-master-0"], status=_status("somebody-else"))


def test_non_strict_only_warns_about_missing_hosts(capsys):
    _check(["my-shop-master-0"], status=_status(), strict=False)

    assert "Not found in" in capsys.readouterr().err


def test_public_project_never_queries_the_inventory(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("inventory queried for a public project")

    monkeypatch.setattr(ansible_commands, "_dynamic_inventory_hostvars", fail)

    ansible_commands._ensure_tailnet(tmp_path, "production", ".shared-roles", {})
