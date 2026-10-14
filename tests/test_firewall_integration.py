"""Exercise CLI, pinning, Ansible and the real role against a local Cloud API."""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLES = Path(
    os.environ.get("STARTUP_TEST_DEPLOY_TEMPLATE", ROOT.parent / "deploy-template")
)


@pytest.fixture
def cloud():
    firewall = {
        "id": 1,
        "name": "shop-firewall",
        "created": "2026-01-01T00:00:00Z",
        "labels": {},
        "applied_to": [],
        "rules": [
            {
                "direction": "in",
                "protocol": "tcp",
                "port": "6443",
                "source_ips": ["192.0.2.1/32"],
                "destination_ips": [],
                "description": "old admin",
            }
        ],
    }
    writes = []
    node = {
        "id": 1,
        "name": "shop-master-0",
        "created": "2026-01-01T00:00:00Z",
        "status": "running",
        "public_net": {
            "ipv4": {"id": 1, "ip": "192.0.2.10", "blocked": False, "dns_ptr": "node"},
            "ipv6": None,
            "floating_ips": [],
            "firewalls": [],
        },
        "private_net": [],
        "image": None,
        "server_type": {"id": 1, "name": "test"},
        "datacenter": {"id": 1, "name": "test", "location": {"id": 1, "name": "test"}},
        "location": {"id": 1, "name": "test"},
        "placement_group": None,
        "rescue_enabled": False,
        "backup_window": None,
        "labels": {},
        "protection": {"delete": False, "rebuild": False},
    }

    class API(BaseHTTPRequestHandler):
        def answer(self, data):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

        def do_GET(self):
            path = urlsplit(self.path).path
            if path.endswith("/servers"):
                self.answer(
                    {
                        "servers": [node],
                        "meta": {
                            "pagination": {"page": 1, "per_page": 50, "next_page": None}
                        },
                    }
                )
            elif path.endswith("/firewalls"):
                self.answer(
                    {
                        "firewalls": [firewall],
                        "meta": {
                            "pagination": {"page": 1, "per_page": 50, "next_page": None}
                        },
                    }
                )
            elif path.endswith("/firewalls/1"):
                self.answer({"firewall": firewall})
            else:
                self.send_error(404)

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            writes.append((self.path, data))
            if self.path.endswith("/set_rules"):
                firewall["rules"] = [
                    {
                        "destination_ips": [],
                        "source_ips": [],
                        "port": None,
                        "description": None,
                        **rule,
                    }
                    for rule in data["rules"]
                ]
                self.answer({"actions": []})
            else:
                self.send_error(400)

        def log_message(self, format, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), API)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", writes, firewall
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("network_range", [None, "10.99.0.0/16"])
def test_firewall_preview_then_close_public_api_only(tmp_path, cloud, network_range):
    if not ROLES.is_dir():
        pytest.fail(
            "requires deploy-template checkout (set STARTUP_TEST_DEPLOY_TEMPLATE)"
        )
    endpoint, writes, firewall = cloud
    # GIVEN an existing cloud firewall and the actual shared role, with local
    # inventories so the only replacement boundary is the Hetzner HTTP API.
    project = tmp_path / "deployment"
    group = project / "group_vars"
    group.mkdir(parents=True)
    (group / "production.yml").write_text(
        yaml.safe_dump(
            {
                "project_name": "shop",
                **({"network_ip_range": network_range} if network_range else {}),
                "firewall_api_source_ips": [],
                "ansible_python_interpreter": sys.executable,
            }
        )
    )
    (project / "inventory.ini").write_text("[production]\nprovision-infrastructure\n")
    (project / "inventory.hcloud.yml").write_text(
        "all:\n  children:\n    production:\n      hosts:\n        provision-infrastructure:\n          ansible_connection: local\n"
    )
    password = tmp_path / "password"
    password.write_text("synthetic-password")
    encrypted = subprocess.run(
        [
            str(Path(sys.executable).parent / "ansible-vault"),
            "encrypt",
            "--vault-password-file",
            str(password),
            "--output",
            "-",
        ],
        input="synthetic-token",
        text=True,
        capture_output=True,
        check=True,
    )
    (project / "hcloud_token_production").write_text(encrypted.stdout)
    password.unlink()
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        "STARTUP_VAULT_PASSWORD": "synthetic-password",
        "HCLOUD_ENDPOINT": endpoint,
        "UV_PROJECT_ENVIRONMENT": str(Path(sys.executable).parent.parent),
        "UV_NO_SYNC": "1",
    }
    command = [
        sys.executable,
        "-m",
        "cli.startup",
        "ansible",
        "firewall",
        "--working-directory",
        str(project),
        "--version",
        subprocess.run(
            ["git", "-C", str(ROLES), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
        "--repo-url",
        ROLES.as_uri(),
    ]
    # WHEN preview runs THEN there are no cloud mutations.
    inspected = subprocess.run(
        [*command, "--inspect"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert inspected.returncode == 0, inspected.stdout + inspected.stderr
    assert '"name": "shop-firewall"' in inspected.stdout
    assert not writes
    preview = subprocess.run(
        command, env=env, capture_output=True, text=True, timeout=120, check=False
    )
    assert preview.returncode == 0, preview.stdout + preview.stderr
    assert not writes
    assert firewall["rules"][0]["port"] == "6443"
    # WHEN executing THEN only set_rules is called and public web/SSH remain.
    applied = subprocess.run(
        [*command, "--execute"],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert applied.returncode == 0, applied.stdout + applied.stderr
    assert [p for p, _ in writes] == ["/v1/firewalls/1/actions/set_rules"]
    ports = {r.get("port") for r in firewall["rules"]}
    assert "6443" not in ports
    assert {"80", "443", "22"} <= ports
    assert any(
        r["source_ips"] == [network_range or "10.0.0.0/16"] for r in firewall["rules"]
    )
    assert not list(project.glob(".startup-firewall-*.yml"))
    assert "synthetic-token" not in applied.stdout + applied.stderr
    # GIVEN an attached startup, WHEN execution is attempted, THEN owner guard
    # rejects it before credentials or provider calls.
    (project / "cluster.yml").write_text("managed: false\nowner: example/owner\n")
    denied = subprocess.run(
        [*command, "--execute"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert denied.returncode != 0
    assert "run it from example/owner" in denied.stderr
    assert len(writes) == 1
