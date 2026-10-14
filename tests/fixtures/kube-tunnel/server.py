"""Only the Kubernetes boundary is replaced; SSH, TLS and kubectl are real."""

import json
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


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


server = ThreadingHTTPServer(("127.0.0.1", 6443), Handler)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.load_cert_chain("/fixtures/server.crt", "/fixtures/server.key")
context.load_verify_locations("/fixtures/ca.crt")
context.verify_mode = ssl.CERT_REQUIRED
server.socket = context.wrap_socket(server.socket, server_side=True)
server.serve_forever()
