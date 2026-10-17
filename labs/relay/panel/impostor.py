#!/usr/bin/env python3
"""T4 impostor: a TLS server presenting a leaf for the household's name that was signed by ANOTHER CA.

Models a hostile network (hotel Wi-Fi, poisoned DNS) or a malicious relay holding a certificate that the panel's
SYSTEM store would accept. Serves one fixed answer and does not ask for a client certificate.
"""
import http.server
import ssl
import sys


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"IMPOSTOR\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def main() -> int:
    cert, key, port = sys.argv[1], sys.argv[2], int(sys.argv[3])
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    ctx.load_cert_chain(cert, key)
    srv = http.server.HTTPServer(("127.0.0.1", port), H)
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    print("impostor ready", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
