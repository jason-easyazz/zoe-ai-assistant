import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe


HELPER_PATH = Path(__file__).resolve().parents[3] / "tools" / "audit" / "ensure_nginx_security_headers.py"
spec = importlib.util.spec_from_file_location("ensure_nginx_security_headers", HELPER_PATH)
if spec is None or spec.loader is None:
    pytest.skip(f"cannot load nginx security-header helper at {HELPER_PATH}", allow_module_level=True)
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


def test_ensure_headers_adds_managed_block_to_each_server():
    config = """# Example only: server { should not be treated as an active block.
map $http_upgrade $connection_upgrade {
    default upgrade;
}

server {
    listen 80;
    server_name _;

    root /usr/share/nginx/html;
    index index.html;

    location ~* \\.(js|css)$ {
        add_header Cache-Control "no-cache, must-revalidate";
        try_files $uri =404;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}

server {
    listen 18790 ssl;
    http2 on;
    server_name _;

    ssl_protocols TLSv1.2 TLSv1.3;

    location / {
        proxy_pass http://host.docker.internal:18789;
    }
}
"""

    updated = helper.ensure_headers(config)

    assert updated.count(helper.BEGIN_MARKER) == 3
    assert updated.count("location ~*") == 1
    assert updated.count("add_header Content-Security-Policy") == 3
    assert updated.count("add_header X-Frame-Options") == 3
    assert updated.count("add_header Strict-Transport-Security") == 1
    assert updated.count("connect-src 'self' ws: wss:") == 3
    assert helper.missing_headers(updated) == []


def test_ensure_headers_ignores_upstream_server_directives():
    config = """upstream backend {
    server 127.0.0.1:8080;
}

server {
    listen 80;
    server_name _;
}
"""

    updated = helper.ensure_headers(config)

    assert "upstream backend {\n    server 127.0.0.1:8080;\n}" in updated
    assert updated.count(helper.BEGIN_MARKER) == 1
    assert helper.missing_headers(updated) == []


def test_ensure_headers_ignores_variable_named_server():
    config = """map $uri $server {
    default backend;
}

server {
    listen 80;
    server_name _;
}
"""

    updated = helper.ensure_headers(config)

    assert "map $uri $server {\n    default backend;\n}" in updated
    assert updated.count(helper.BEGIN_MARKER) == 1
    assert helper.missing_headers(updated) == []


def test_ensure_headers_ignores_braces_inside_quoted_values():
    config = """server {
    listen 80;
    server_name _;
    add_header X-Custom "see /{path" always;

    location / {
        try_files $uri $uri/ /index.html;
    }
}
"""

    updated = helper.ensure_headers(config)

    assert 'add_header X-Custom "see /{path" always;' in updated
    assert updated.count(helper.BEGIN_MARKER) == 1
    assert helper.missing_headers(updated) == []


def test_ensure_headers_ignores_braces_inside_comment_lines():
    config = """server {
    listen 80;
    server_name _;
    # see /etc/nginx/conf.d/ {

    location / {
        try_files $uri $uri/ /index.html;
    }
}
"""

    updated = helper.ensure_headers(config)

    assert "# see /etc/nginx/conf.d/ {" in updated
    assert updated.count(helper.BEGIN_MARKER) == 1
    assert helper.missing_headers(updated) == []


def test_ensure_headers_uses_existing_location_child_indent():
    config = """server {
  listen 443 ssl;
  server_name _;

  location ~* \\.(js|css)$ {
    add_header Cache-Control "no-cache";
    try_files $uri =404;
  }
}
"""

    updated = helper.ensure_headers(config)

    assert "    # BEGIN ZOE MANAGED SECURITY HEADERS" in updated
    assert "        # BEGIN ZOE MANAGED SECURITY HEADERS" not in updated
    assert helper.missing_headers(updated) == []


def test_ensure_headers_ignores_location_index_when_placing_server_headers():
    config = """server {
    listen 80;
    server_name _;

    location / {
        index index.html;
        try_files $uri $uri/ /index.html;
    }
}
"""

    updated = helper.ensure_headers(config)
    server_marker = updated.index(helper.BEGIN_MARKER)
    location_start = updated.index("    location /")
    location_index = updated.index("        index index.html;")

    assert server_marker < location_start
    assert helper.BEGIN_MARKER not in updated[location_start:location_index]
    assert helper.missing_headers(updated) == []


def test_ensure_headers_places_server_headers_after_last_known_preamble_directive():
    config = """server {
    listen 80;
    server_name _;

    location / {
        try_files $uri $uri/ /index.html;
    }
}
"""

    updated = helper.ensure_headers(config)

    assert updated.index("server_name _;") < updated.index(helper.BEGIN_MARKER)
    assert updated.index("listen 80;") < updated.index(helper.BEGIN_MARKER)


def test_ensure_headers_places_server_headers_after_late_listen_when_index_comes_first():
    config = """server {
    index index.html;
    listen 80;
    server_name _;

    location / {
        try_files $uri $uri/ /index.html;
    }
}
"""

    updated = helper.ensure_headers(config)

    assert updated.index("server_name _;") < updated.index(helper.BEGIN_MARKER)
    assert updated.index("listen 80;") < updated.index(helper.BEGIN_MARKER)


def test_ensure_headers_places_server_headers_after_tls_certificate_directives():
    config = """server {
    listen 443 ssl;
    server_name _;
    ssl_certificate /etc/ssl/cert.pem;
    ssl_certificate_key /etc/ssl/key.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
    }
}
"""

    updated = helper.ensure_headers(config)

    assert updated.index("ssl_certificate_key /etc/ssl/key.pem;") < updated.index(helper.BEGIN_MARKER)
    assert "Strict-Transport-Security" in updated


def test_ensure_headers_uses_listen_as_minimal_server_anchor():
    config = """server {
    listen 8080;

    location / {
        proxy_pass http://127.0.0.1:8000;
    }
}
"""

    updated = helper.ensure_headers(config)

    assert updated.index("listen 8080;") < updated.index(helper.BEGIN_MARKER)
    assert helper.missing_headers(updated) == []


def test_ensure_headers_does_not_treat_ssl_comment_as_tls():
    config = """server {
    listen 80;
    # redirect to ssl happens elsewhere

    location / {
        proxy_set_header X-Forwarded-Proto ssl;
        proxy_pass http://127.0.0.1:8000;
    }
}
"""

    updated = helper.ensure_headers(config)

    assert "Strict-Transport-Security" not in updated
    assert helper.missing_headers(updated) == []


def test_ensure_headers_adds_location_headers_when_location_defines_add_header():
    config = """server {
    listen 443 ssl;
    server_name _;

    location ~* \\.(js|css)$ {
        add_header Cache-Control "no-cache, must-revalidate";
        try_files $uri =404;
    }
}
"""

    updated = helper.ensure_headers(config)

    assert updated.count(helper.BEGIN_MARKER) == 2
    assert updated.count("add_header Content-Security-Policy") == 2
    assert updated.count("add_header Strict-Transport-Security") == 2
    assert helper.missing_headers(updated) == []


def test_ensure_headers_ignores_commented_add_header_in_location():
    config = """server {
    listen 80;
    server_name _;

    location /assets/ {
        # add_header Cache-Control "no-cache";
        try_files $uri =404;
    }
}
"""

    updated = helper.ensure_headers(config)
    location_start = updated.index("    location /assets/")
    location = updated[location_start:]

    assert updated.count(helper.BEGIN_MARKER) == 1
    assert helper.BEGIN_MARKER not in location
    assert helper.missing_headers(updated) == []


def test_ensure_headers_is_idempotent_and_replaces_managed_blocks():
    config = """server {
    listen 80;
    server_name _;

    # BEGIN ZOE MANAGED SECURITY HEADERS
    add_header X-Frame-Options "DENY" always;
    # END ZOE MANAGED SECURITY HEADERS

    location / {
        try_files $uri $uri/ /index.html;
    }
}
"""

    once = helper.ensure_headers(config)
    twice = helper.ensure_headers(once)

    assert once == twice
    assert '"DENY"' not in once
    assert '"SAMEORIGIN"' in once
    assert "connect-src 'self' ws: wss:" in once
    assert once.count(helper.BEGIN_MARKER) == 1
    assert "Strict-Transport-Security" not in once
    assert helper.missing_headers(once) == []


def test_ensure_headers_replaces_managed_csp_missing_websocket_sources():
    config = """server {
    listen 80;
    server_name _;

    # BEGIN ZOE MANAGED SECURITY HEADERS
    add_header Content-Security-Policy "default-src 'self'; connect-src 'self';" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "camera=(), microphone=(self), geolocation=()" always;
    # END ZOE MANAGED SECURITY HEADERS

    location / {
        try_files $uri $uri/ /index.html;
    }
}
"""

    updated = helper.ensure_headers(config)

    assert "connect-src 'self' ws: wss:" in updated
    assert "connect-src 'self';" not in updated
    assert helper.missing_headers(updated) == []


def test_ensure_headers_is_idempotent_from_fresh_config_with_blank_before_location():
    config = """server {
    listen 80;
    server_name _;

    location / {
        try_files $uri $uri/ /index.html;
    }
}
"""

    once = helper.ensure_headers(config)
    twice = helper.ensure_headers(once)

    assert once == twice
    assert once.count(helper.BEGIN_MARKER) == 1
    assert "\n\n    location /" in once
    assert helper.missing_headers(once) == []


def test_ensure_headers_is_idempotent_with_location_managed_blocks():
    config = """server {
    listen 443 ssl;
    server_name _;

    location ~* \\.(js|css)$ {
        add_header Cache-Control "no-cache";
        try_files $uri =404;
    }
}
"""

    once = helper.ensure_headers(config)
    twice = helper.ensure_headers(once)

    assert once == twice
    assert once.count(helper.BEGIN_MARKER) == 2
    assert helper.missing_headers(once) == []


def test_missing_headers_reports_per_server_block():
    config = """server {
    listen 80;
    server_name _;
}
"""

    missing = helper.missing_headers(config)

    assert "server[1]: Content-Security-Policy" in missing
    assert "server[1]: Permissions-Policy" in missing


def test_missing_headers_requires_server_level_headers_when_location_has_headers():
    config = """server {
    listen 80;

    location ~* \\.(js|css)$ {
        # BEGIN ZOE MANAGED SECURITY HEADERS
        add_header Content-Security-Policy "default-src 'self'" always;
        add_header X-Frame-Options "SAMEORIGIN" always;
        add_header X-Content-Type-Options "nosniff" always;
        add_header Referrer-Policy "strict-origin-when-cross-origin" always;
        add_header Permissions-Policy "camera=(), microphone=(self), geolocation=()" always;
        # END ZOE MANAGED SECURITY HEADERS
        add_header Cache-Control "no-cache";
    }
}
"""

    missing = helper.missing_headers(config)

    assert "server[1]: Content-Security-Policy" in missing
    assert "server[1].location[1]: Content-Security-Policy" not in missing


def test_missing_headers_does_not_match_header_name_inside_another_value():
    config = """server {
    listen 80;
    add_header X-Debug "add_header Content-Security-Policy removed" always;
}
"""

    missing = helper.missing_headers(config)

    assert "server[1]: Content-Security-Policy" in missing


def test_main_writes_with_atomic_temp_path(tmp_path):
    config_path = tmp_path / "nginx.conf"
    config_path.write_text("server {\n    listen 80;\n}\n", encoding="utf-8")

    result = helper.main(["--path", str(config_path)])

    assert result == 0
    assert helper.BEGIN_MARKER in config_path.read_text(encoding="utf-8")
    assert not (tmp_path / ".nginx.conf.tmp").exists()


def test_main_check_fails_when_no_server_blocks(tmp_path, capsys):
    config_path = tmp_path / "nginx.conf"
    config_path.write_text("events {}\n", encoding="utf-8")

    result = helper.main(["--path", str(config_path), "--check"])
    captured = capsys.readouterr()

    assert result == 1
    assert "no nginx server blocks found" in captured.err
    assert "security headers present in 0 server block" not in captured.out


def test_main_returns_clean_error_for_malformed_config(tmp_path, capsys):
    config_path = tmp_path / "nginx.conf"
    config_path.write_text("server {\n    listen 80;\n", encoding="utf-8")

    result = helper.main(["--path", str(config_path)])
    captured = capsys.readouterr()

    assert result == 1
    assert "error: unterminated nginx server block" in captured.err
    assert "Traceback" not in captured.err


def test_main_returns_clean_error_for_missing_file(tmp_path, capsys):
    config_path = tmp_path / "missing-nginx.conf"

    result = helper.main(["--path", str(config_path), "--check"])
    captured = capsys.readouterr()

    assert result == 1
    assert "error: cannot read" in captured.err
    assert str(config_path) in captured.err
    assert "Traceback" not in captured.err
