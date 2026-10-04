"""The UI edge's delivery-layer contracts (2026-10-04 UI deep review, wave 2).

Pure text pins over services/zoe-ui/nginx.conf, nginx.d/*.inc, docker-compose.yml and the
workflows — the things that silently drifted when the two server blocks were hand-copied:
one shared body, one security-header snippet in lockstep with the audit tool, every proxy
prefix ``^~`` (so the ``.js``/``.css``/``.json`` regexes can never capture a proxied path),
streams never gzipped, a real 404 instead of index.html, no retired-service proxies, and a
deploy that actually restarts the container (the conf is an inode-pinned file mount).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

ROOT = Path(__file__).resolve().parents[2]
UI = ROOT / "services" / "zoe-ui"
CONF = (UI / "nginx.conf").read_text(encoding="utf-8")
LOCATIONS = (UI / "nginx.d" / "locations.inc").read_text(encoding="utf-8")
HEADERS = (UI / "nginx.d" / "security-headers.inc").read_text(encoding="utf-8")
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
DEPLOY = (ROOT / ".github" / "workflows" / "deploy.yml").read_text(encoding="utf-8")
VALIDATE = (ROOT / ".github" / "workflows" / "validate.yml").read_text(encoding="utf-8")

SNIPPET = "include /etc/nginx/zoe/security-headers.inc;"


def _active(text: str) -> list[str]:
    return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]


def _location_blocks(text: str) -> list[tuple[str, str]]:
    """(header, body) for every top-level location in a snippet."""
    out = []
    for m in re.finditer(r"^location\s+([^{]+)\{", text, re.M):
        depth, i = 0, m.end() - 1
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        out.append((m.group(1).strip(), text[m.end():i]))
    return out


def test_both_server_blocks_share_one_body_and_one_header_snippet():
    servers = re.findall(r"^server\s*\{", CONF, re.M)
    assert len(servers) == 2, "exactly :80 and :443 — the OpenClaw :18790 server is retired"
    assert CONF.count(SNIPPET) == 2
    assert CONF.count("include /etc/nginx/zoe/locations.inc;") == 2
    directives = [l.split()[0] for l in _active(CONF)]
    assert "add_header" not in directives, "headers live ONLY in the snippet"
    assert "location" not in directives, "locations live ONLY in locations.inc"


def test_hsts_is_tls_only_via_the_scheme_map():
    assert re.search(r"map \$scheme \$zoe_hsts \{\s*https \"max-age=31536000; includeSubDomains\";\s*default \"\";", CONF)
    assert "add_header Strict-Transport-Security $zoe_hsts always;" in HEADERS


def test_snippet_csp_matches_the_audit_tool():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "ensure_nginx_security_headers", ROOT / "tools" / "audit" / "ensure_nginx_security_headers.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    tool_csp = dict(mod.SECURITY_HEADERS)["Content-Security-Policy"]
    m = re.search(r'add_header Content-Security-Policy "([^"]+)" always;', HEADERS)
    assert m and m.group(1) == tool_csp, "nginx.d/security-headers.inc and the tool drifted"
    for name, _ in mod.SECURITY_HEADERS:
        assert f"add_header {name} " in HEADERS


def test_csp_has_no_eval_no_youtube_and_allows_the_panel_daemon():
    csp = re.search(r'Content-Security-Policy "([^"]+)"', HEADERS).group(1)
    assert "'unsafe-eval'" not in csp
    assert "youtube" not in csp
    connect = re.search(r"connect-src ([^;]+);", csp).group(1).split()
    assert connect == ["'self'", "ws:", "wss:", "http://localhost:7777", "http://127.0.0.1:8765"]
    assert "media-src 'self' blob: data:" in csp


def test_every_location_that_sets_cache_control_reincludes_the_headers():
    offenders = [h for h, body in _location_blocks(LOCATIONS) if "add_header" in body and SNIPPET not in body]
    assert offenders == [], f"add_header drops inherited headers; these locations lost the CSP: {offenders}"


def test_caching_policy_is_explicit_for_every_first_party_type():
    blocks = dict(_location_blocks(LOCATIONS))
    assert 'add_header Cache-Control "no-cache, must-revalidate" always;' in blocks["= /sw.js"]
    assert 'add_header Cache-Control "no-cache, must-revalidate" always;' in blocks[r"~* \.(js|css)$"]
    assert 'add_header Cache-Control "no-cache, must-revalidate" always;' in blocks[r"~* \.(html|json|webmanifest)$"]
    assert "max-age=604800" in blocks[r"~* \.(woff2?|ttf|png|ico|svg|jpe?g|webp|gif)$"]


def test_proxy_prefixes_are_all_anchored_so_regexes_cannot_capture_them():
    for header, body in _location_blocks(LOCATIONS):
        if "proxy_pass" not in body:
            continue
        assert header.startswith("^~ ") or header.startswith("= "), (
            f"location {header}: a plain prefix lets the .js/.css/.json regexes win (404'd Music Assistant assets)"
        )


def test_streams_are_never_gzipped():
    blocks = dict(_location_blocks(LOCATIONS))
    for key in ("^~ /api/", "^~ /ws/", "^~ /livekit/", "^~ /multica-api/"):
        assert "gzip off;" in blocks[key], key
    assert "gzip on;" in CONF
    types = re.search(r"gzip_types ([^;]+);", CONF).group(1)
    assert "text/event-stream" not in types and "ndjson" not in types and "text/plain" not in types


def test_missing_pages_are_404_not_index_html():
    blocks = dict(_location_blocks(LOCATIONS))
    assert "try_files $uri $uri/ =404;" in blocks["/"]
    assert "/index.html" not in blocks["/"]
    assert "error_page 404 /404.html;" in LOCATIONS
    assert (UI / "dist" / "404.html").is_file()


def test_retired_services_are_gone_everywhere():
    for needle in ("/hermes/", "/proxy/hermes/", "/proxy/openclaw/", "18789", "18790", ":8642"):
        assert needle not in CONF and needle not in LOCATIONS, needle
    assert "18790" not in COMPOSE


def test_compose_mounts_the_snippet_directory():
    assert "- ./services/zoe-ui/nginx.d:/etc/nginx/zoe:ro" in COMPOSE
    assert "- ./services/zoe-ui/nginx.conf:/etc/nginx/conf.d/default.conf:ro" in COMPOSE


def test_deploy_restarts_the_container_when_nginx_changes_and_preflights_the_new_files():
    step = DEPLOY[DEPLOY.index("Restart zoe-ui / nginx (if changed)"):]
    step = step[: step.index("- name:", 10)]
    assert "services/zoe-ui/nginx.conf services/zoe-ui/nginx.d docker-compose.yml" in step
    assert "nginx:alpine nginx -t" in step
    assert "docker compose up -d --no-deps zoe-ui" in step
    assert "docker restart zoe-ui" in step


def test_validate_parses_the_real_conf_with_a_negative_control():
    step = VALIDATE[VALIDATE.index("nginx config must parse"):]
    step = step[: step.index("- name:", 10)]
    assert "nginx:alpine nginx -t" in step
    assert "broken.conf" in step and "the check is not checking" in step
