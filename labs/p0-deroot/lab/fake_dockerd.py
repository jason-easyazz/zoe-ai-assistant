#!/usr/bin/env python3
"""Lab stand-in for dockerd: an HTTP server on /var/run/docker.sock (root:docker 0660) that answers 200 to every
request and logs the method + path, so a cell can tell "reached the daemon" (200) from "the proxy refused" (403/405).
It runs nothing. Lab-only."""
import grp
import http.server
import os
import socketserver

SOCK = "/var/run/docker.sock"


class H(http.server.BaseHTTPRequestHandler):
    def _any(self):
        with open("/run/p0lab-dockerd.log", "a") as log:
            log.write(f"{self.command} {self.path}\n")
        body = b'{"reached":"fake-dockerd"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = do_POST = do_HEAD = do_DELETE = do_PUT = _any

    def address_string(self):
        return "unix"

    def log_message(self, *a):
        pass


class S(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True


if __name__ == "__main__":
    if os.path.exists(SOCK):
        os.unlink(SOCK)
    srv = S(SOCK, H)
    os.chown(SOCK, 0, grp.getgrnam("docker").gr_gid)
    os.chmod(SOCK, 0o660)
    srv.serve_forever()
