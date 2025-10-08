#!/usr/bin/env python3
"""
Simple proxy server to handle auth service requests and serve UI
This solves the mixed content issues by serving everything on the same origin
"""

import http.server
import socketserver
import urllib.request
import urllib.parse
import json
import os
from urllib.error import URLError

class ProxyHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory="/home/pi/zoe/services/zoe-ui/dist", **kwargs)
    
    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization, X-Session-ID')
        super().end_headers()
    
    def do_OPTIONS(self):
        self.send_response(200)
        self.end_headers()
    
    def do_GET(self):
        if self.path.startswith('/api/auth/') or self.path.startswith('/health'):
            self.proxy_request()
        else:
            super().do_GET()
    
    def do_POST(self):
        if self.path.startswith('/api/auth/'):
            self.proxy_request()
        else:
            self.send_error(404)
    
    def proxy_request(self):
        try:
            # Get the request body for POST requests
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length) if content_length > 0 else None
            
            # Build the target URL
            auth_url = f"http://localhost:8002{self.path}"
            
            # Create the request
            headers = {}
            for key, value in self.headers.items():
                if key.lower() not in ['host', 'content-length']:
                    headers[key] = value
            
            req = urllib.request.Request(auth_url, data=post_data, headers=headers, method=self.command)
            
            # Make the request to auth service
            with urllib.request.urlopen(req) as response:
                # Send response
                self.send_response(response.getcode())
                
                # Copy headers
                for key, value in response.headers.items():
                    if key.lower() not in ['content-length', 'connection', 'transfer-encoding']:
                        self.send_header(key, value)
                
                self.end_headers()
                
                # Copy body
                self.wfile.write(response.read())
                
        except URLError as e:
            print(f"Auth service error: {e}")
            # Return demo data if auth service is down
            if 'profiles' in self.path:
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                demo_profiles = [
                    {"user_id": "demo_admin", "username": "Admin", "role": "admin", "avatar": "A"},
                    {"user_id": "demo_user", "username": "User", "role": "user", "avatar": "U"}
                ]
                self.wfile.write(json.dumps(demo_profiles).encode())
            else:
                self.send_error(503, "Auth service unavailable")
        except Exception as e:
            print(f"Proxy error: {e}")
            self.send_error(500, "Proxy error")

if __name__ == "__main__":
    PORT = 8090
    with socketserver.TCPServer(("", PORT), ProxyHandler) as httpd:
        print(f"🌐 Zoe UI Server with Auth Proxy running on http://localhost:{PORT}")
        print("📱 Access your application at: http://localhost:8090")
        print("🔐 Auth requests automatically proxied to localhost:8002")
        print("\nDefault credentials:")
        print("  Admin: admin / admin")
        print("  User:  user / user")
        print("\nPress Ctrl+C to stop")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n👋 Server stopped")
