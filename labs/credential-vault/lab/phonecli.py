"""The member's phone, reduced to HTTP over the phone unit's Unix socket (what nginx would forward).
Usage: phonecli.py SOCKET METHOD PATH [form-urlencoded body]. Prints one JSON line {status, body}."""

import http.client
import json
import socket
import sys


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("phone.lab.test", timeout=300)
        self.unix_path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(300)
        self.sock.connect(self.unix_path)


path, method, url = sys.argv[1], sys.argv[2], sys.argv[3]
body = sys.argv[4] if len(sys.argv) > 4 else None
if body == "-":
    body = sys.stdin.read()          # form bodies (passwords, codes) never go on the command line
try:
    c = UnixHTTPConnection(path)
    headers = {"Content-Type": "application/x-www-form-urlencoded"} if body is not None else {}
    c.request(method, url, body=body, headers=headers)
    r = c.getresponse()
    print(json.dumps({"status": r.status, "body": r.read().decode("utf-8", "replace")}))
except OSError as e:
    print(json.dumps({"status": 0, "body": "connect:" + type(e).__name__}))
