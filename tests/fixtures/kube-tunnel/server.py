"""Only the Kubernetes boundary is replaced; SSH, TLS and kubectl are real."""

import json
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/api":
            data = {
                "kind": "APIVersions",
                "apiVersion": "v1",
                "versions": ["v1"],
                "serverAddressByClientCIDRs": [],
            }
        elif path == "/apis":
            data = {"kind": "APIGroupList", "apiVersion": "v1", "groups": []}
        elif path == "/api/v1":
            data = {
                "kind": "APIResourceList",
                "apiVersion": "v1",
                "groupVersion": "v1",
                "resources": [
                    {
                        "name": "pods",
                        "singularName": "pod",
                        "namespaced": True,
                        "kind": "Pod",
                        "verbs": ["get", "list"],
                    }
                ],
            }
        elif path.endswith("/pods"):
            namespace = path.split("/")[4]
            data = {
                "kind": "PodList",
                "apiVersion": "v1",
                "metadata": {},
                "items": [
                    {
                        "kind": "Pod",
                        "apiVersion": "v1",
                        "metadata": {
                            "name": namespace + "-backend",
                            "namespace": namespace,
                        },
                        "spec": {
                            "containers": [{"name": "backend", "image": "example"}]
                        },
                        "status": {"phase": "Running"},
                    }
                ],
            }
        elif path == "/readyz":
            data = {"ready": True}
        else:
            data = {"gitVersion": "v1.36.0", "major": "1", "minor": "36"}
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        pass


class Server(ThreadingHTTPServer):
    def get_request(self):
        sock, address = self.socket.accept()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain("/fixtures/server.crt", "/fixtures/server.key")
        trust = Path("/var/lib/rancher/k3s/server/tls/client-ca.crt")
        context.load_verify_locations(
            str(trust) if trust.exists() else "/fixtures/ca.crt"
        )
        context.verify_mode = ssl.CERT_REQUIRED
        try:
            return context.wrap_socket(sock, server_side=True), address
        except ssl.SSLError:
            sock.close()
            raise


server = Server(("127.0.0.1", 6443), Handler)
server.serve_forever()
