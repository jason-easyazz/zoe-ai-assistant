"""Simple enhanced backend server for v3.1 deployments."""

from http.server import HTTPServer, SimpleHTTPRequestHandler


def run() -> None:
    """Start a basic HTTP server."""
    server = HTTPServer(("0.0.0.0", 8000), SimpleHTTPRequestHandler)
    print("Enhanced backend running on http://0.0.0.0:8000")
    server.serve_forever()


if __name__ == "__main__":
    run()
