#!/usr/bin/python3
"""K3s boundary stand-in; certificate generation and TLS verification are real."""

import base64
import json
import shutil
import subprocess
import sys
from pathlib import Path

args = sys.argv[1:]
server = Path("/var/lib/rancher/k3s/server")
if args[:3] == ["kubectl", "get", "nodes"]:
    count = int(Path("/fixtures/node-count").read_text())
    print(json.dumps({"items": [{} for _ in range(count)]}))
elif args[:2] == ["etcd-snapshot", "save"]:
    (server / "snapshot-created").touch()
elif args[:2] == ["certificate", "rotate-ca"]:
    stage = Path(args[args.index("--path") + 1])
    for name in ("client-ca.crt", "client-ca.key"):
        shutil.copyfile(stage / "tls" / name, server / "tls" / name)
elif args[:2] == ["certificate", "rotate"]:
    # K3s StringSlice flags require repetitions; a comma is one invalid service.
    if args.count("--service") != 9 or any("," in value for value in args):
        sys.exit(2)
    commands = [
        [
            "openssl",
            "req",
            "-new",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            "/fixtures/rotated.key",
            "-out",
            "/fixtures/rotated.csr",
            "-subj",
            "/CN=rotated-admin",
        ],
        [
            "openssl",
            "x509",
            "-req",
            "-in",
            "/fixtures/rotated.csr",
            "-CA",
            str(server / "tls/client-ca.crt"),
            "-CAkey",
            str(server / "tls/client-ca.key"),
            "-CAcreateserial",
            "-out",
            "/fixtures/rotated.crt",
            "-days",
            "1",
            "-extfile",
            "/fixtures/client.ext",
        ],
    ]
    for command in commands:
        subprocess.run(command, capture_output=True, check=True)
    # JSON is also valid YAML, avoiding a parser dependency inside the SSH image.
    config = json.loads(Path("/fixtures/source.json").read_text())
    config["users"][0]["user"] = {
        "client-certificate-data": base64.b64encode(
            Path("/fixtures/rotated.crt").read_bytes()
            + (server / "tls/client-ca.crt").read_bytes()
        ).decode(),
        "client-key-data": base64.b64encode(
            Path("/fixtures/rotated.key").read_bytes()
        ).decode(),
    }
    Path("/etc/rancher/k3s/k3s.yaml").write_text(json.dumps(config))
elif args[:3] == ["kubectl", "get", "--raw=/readyz"]:
    print("ok")
else:
    sys.exit(1)
