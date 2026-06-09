#!/usr/bin/env python3
"""Add or verify managed HTTP security headers in nginx server blocks.

The helper targets Zoe's nginx config shape: top-level server blocks with
direct child location blocks. It does not recursively rewrite nested location
blocks, which Zoe's nginx.conf does not use.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = PROJECT_ROOT / "services" / "zoe-ui" / "nginx.conf"

BEGIN_MARKER = "# BEGIN ZOE MANAGED SECURITY HEADERS"
END_MARKER = "# END ZOE MANAGED SECURITY HEADERS"

SECURITY_HEADERS: tuple[tuple[str, str], ...] = (
    (
        "Content-Security-Policy",
        # The legacy Zoe SPA and proxied Multica surface still need inline/eval script
        # compatibility. Keep this explicit so future security audits can tighten it.
        "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data: https:; "
        "font-src 'self'; connect-src 'self' ws: wss:; frame-ancestors 'self';",
    ),
    ("X-Frame-Options", "SAMEORIGIN"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "strict-origin-when-cross-origin"),
    ("Permissions-Policy", "camera=(), microphone=(self), geolocation=()"),
)
HSTS_HEADER = ("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
TLS_LISTEN_RE = re.compile(r"^\s*listen\b[^;]*\b(?:443|ssl)\b", re.MULTILINE)
SSL_CERTIFICATE_RE = re.compile(r"^\s*ssl_certificate\b", re.MULTILINE)


def _managed_block(indent: str = "    ", *, include_hsts: bool = False) -> str:
    headers = SECURITY_HEADERS + ((HSTS_HEADER,) if include_hsts else ())
    lines = [f"{indent}{BEGIN_MARKER}"]
    for name, value in headers:
        lines.append(f'{indent}add_header {name} "{value}" always;')
    lines.append(f"{indent}{END_MARKER}")
    return "\n".join(lines)


def _line_is_commented(text: str, start: int) -> bool:
    line_start = text.rfind("\n", 0, start) + 1
    return text[line_start:start].lstrip().startswith("#")


def _find_named_blocks(text: str, name: str) -> list[tuple[int, int]]:
    blocks: list[tuple[int, int]] = []
    index = 0
    while True:
        start = text.find(name, index)
        if start == -1:
            break
        before = text[start - 1] if start else "\n"
        after = text[start + len(name)] if start + len(name) < len(text) else ""
        if (before.isalnum() or before in "_-$") or (after.isalnum() or after in "_-"):
            index = start + len(name)
            continue
        if _line_is_commented(text, start):
            index = start + len(name)
            continue
        brace = text.find("{", start + len(name))
        candidate = text[start:brace] if brace != -1 else ""
        if brace == -1 or ";" in candidate or not candidate.strip().startswith(name):
            index = start + len(name)
            continue
        depth = 0
        pos = brace
        quote: str | None = None
        escaped = False
        while pos < len(text):
            char = text[pos]
            if quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
                pos += 1
                continue
            if char in {'"', "'"}:
                quote = char
                pos += 1
                continue
            if char == "#":
                newline = text.find("\n", pos)
                if newline == -1:
                    break
                pos = newline + 1
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    blocks.append((start, pos + 1))
                    index = pos + 1
                    break
            pos += 1
        else:
            raise ValueError(f"unterminated nginx {name} block")
    return blocks


def _find_server_blocks(text: str) -> list[tuple[int, int]]:
    return _find_named_blocks(text, "server")


def _strip_managed_block(block: str) -> str:
    while True:
        start = block.find(BEGIN_MARKER)
        if start == -1:
            return block
        end = block.find(END_MARKER, start)
        if end == -1:
            raise ValueError("managed security header block is missing its end marker")
        end += len(END_MARKER)
        if end < len(block) and block[end] == "\n":
            end += 1
        line_start = block.rfind("\n", 0, start) + 1
        if not block[line_start:start].strip():
            start = line_start
            if start > 0 and block[start - 1] == "\n":
                start -= 1
        block = block[:start] + block[end:]


def _is_tls_block(block: str) -> bool:
    return bool(TLS_LISTEN_RE.search(block) or SSL_CERTIFICATE_RE.search(block))


def _directive_indent(line: str, fallback: str) -> str:
    stripped = line.lstrip()
    if not stripped:
        return fallback
    return line[: len(line) - len(stripped)]


def _has_active_add_header(block: str) -> bool:
    for line in block.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith("add_header "):
            return True
    return False


def _has_active_header(block: str, name: str) -> bool:
    needle = f"add_header {name} "
    for line in block.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("#"):
            continue
        if stripped.startswith(needle):
            return True
    return False


def _insert_after_header(block: str, *, include_hsts: bool) -> str:
    lines = block.splitlines()
    location_blocks = _find_named_blocks(block, "location")
    first_location_start = min((start for start, _end in location_blocks), default=len(block))
    server_scope_line_count = len(block[:first_location_start].splitlines())
    insert_after = None
    indent = "    "
    for idx, line in enumerate(lines[:server_scope_line_count]):
        stripped = line.strip()
        if stripped.startswith(
            (
                "index ",
                "server_name ",
                "ssl_certificate ",
                "ssl_certificate_key ",
                "ssl_prefer_server_ciphers ",
                "ssl_protocols ",
                "listen ",
            )
        ) and stripped.endswith(";"):
            insert_after = idx
            indent = _directive_indent(line, indent)
    if insert_after is None:
        insert_after = 0

    lines.insert(insert_after + 1, "")
    lines.insert(insert_after + 2, _managed_block(indent=indent, include_hsts=include_hsts))
    return "\n".join(lines)


def _insert_location_headers(block: str, *, include_hsts: bool) -> str:
    location_blocks = _find_named_blocks(block, "location")
    result: list[str] = []
    cursor = 0
    for start, end in location_blocks:
        result.append(block[cursor:start])
        location = block[start:end]
        if not _has_active_add_header(location):
            result.append(location)
            cursor = end
            continue
        lines = location.splitlines()
        insert_after = 0
        location_indent = _directive_indent(lines[0], "    ")
        child_indent = location_indent + "    "
        for line in lines[1:]:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                child_indent = _directive_indent(line, child_indent)
                break
        lines.insert(insert_after + 1, _managed_block(indent=child_indent, include_hsts=include_hsts))
        lines.insert(insert_after + 2, "")
        result.append("\n".join(lines))
        cursor = end
    result.append(block[cursor:])
    return "".join(result)


def ensure_headers(text: str) -> str:
    blocks = _find_server_blocks(text)
    if not blocks:
        raise ValueError("no nginx server blocks found")
    result: list[str] = []
    cursor = 0
    for start, end in blocks:
        result.append(text[cursor:start])
        block = _strip_managed_block(text[start:end])
        include_hsts = _is_tls_block(block)
        block = _insert_after_header(block, include_hsts=include_hsts)
        block = _insert_location_headers(block, include_hsts=include_hsts)
        result.append(block)
        cursor = end
    result.append(text[cursor:])
    updated = "".join(result)
    if text.endswith("\n") and not updated.endswith("\n"):
        updated += "\n"
    return updated


def missing_headers(text: str) -> list[str]:
    missing: list[str] = []
    for index, (start, end) in enumerate(_find_server_blocks(text), start=1):
        block = text[start:end]
        headers = SECURITY_HEADERS + ((HSTS_HEADER,) if _is_tls_block(block) else ())
        location_blocks = _find_named_blocks(block, "location")
        first_location_start = min((loc_start for loc_start, _loc_end in location_blocks), default=len(block))
        server_scope = block[:first_location_start]
        for name, _value in headers:
            if not _has_active_header(server_scope, name):
                missing.append(f"server[{index}]: {name}")
        for location_index, (loc_start, loc_end) in enumerate(location_blocks, start=1):
            location = block[loc_start:loc_end]
            if not _has_active_add_header(location):
                continue
            for name, _value in headers:
                if not _has_active_header(location, name):
                    missing.append(f"server[{index}].location[{location_index}]: {name}")
    return missing


def _write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=DEFAULT_PATH)
    parser.add_argument("--check", action="store_true", help="verify headers without writing")
    args = parser.parse_args(argv)

    path = args.path
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        return 1

    try:
        if args.check:
            blocks = _find_server_blocks(text)
            if not blocks:
                print(f"error: no nginx server blocks found in {path}", file=sys.stderr)
                return 1
            missing = missing_headers(text)
            if missing:
                print("missing managed security headers:")
                for item in missing:
                    print(f"- {item}")
                return 1
            print(f"security headers present in {len(blocks)} server block(s)")
            return 0

        updated = ensure_headers(text)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if updated == text:
        print(f"security headers already present: {path}")
        return 0
    _write_text_atomic(path, updated)
    print(f"updated nginx security headers: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
