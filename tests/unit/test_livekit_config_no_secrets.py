"""The tracked LiveKit server config must never carry the API key pair.

History: `services/livekit/config.yaml` shipped the live `keys:` block — key id
plus base64 secret — in plaintext for four commits from 2026-05. ggshield never
saw it (LiveKit keys have no vendor pattern, so there is nothing for a secret
scanner to match on), which is exactly why this needs a shape check of its own.

The keys now arrive at runtime through `LIVEKIT_KEYS`, interpolated by compose
from the untracked repo-root `.env`. Verified against the pinned image
(livekit-server v1.9.3, `--keys ... [$LIVEKIT_KEYS]`): env/CLI is applied after
the YAML config and replaces any `keys:` in it, and with neither source the
server refuses to start ("one of key-file or keys must be provided") — so this
config cannot silently fall back to an embedded credential.

Runbook: docs/knowledge/livekit-key-rotation.md
"""

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.ci_safe

REPO_ROOT = Path(__file__).resolve().parents[2]
LIVEKIT_CONFIG = REPO_ROOT / "services" / "livekit" / "config.yaml"
COMPOSE = REPO_ROOT / "docker-compose.yml"

# A LiveKit secret is 43 base64url chars; a key id is shorter but still opaque.
# Anything this long and this alphabet-y in a serving config is a credential.
SECRET_SHAPED = re.compile(r"[A-Za-z0-9_+/=-]{24,}")


def test_livekit_config_has_no_keys_block():
    """No `keys:` mapping with entries — the whole point of the change."""
    parsed = yaml.safe_load(LIVEKIT_CONFIG.read_text()) or {}
    assert not parsed.get("keys"), (
        "services/livekit/config.yaml carries a populated `keys:` block. "
        "API keys belong in the untracked .env and reach the container via "
        "LIVEKIT_KEYS — see docs/knowledge/livekit-key-rotation.md."
    )
    assert "key_file" not in parsed, (
        "`key_file` points at an on-disk credential file; this deployment "
        "supplies keys via the LIVEKIT_KEYS env var instead."
    )


def _scalars(node):
    """Every scalar VALUE in the parsed document, keys excluded."""
    if isinstance(node, dict):
        for value in node.values():
            yield from _scalars(value)
    elif isinstance(node, (list, tuple)):
        for item in node:
            yield from _scalars(item)
    elif node is not None:
        yield str(node)


def test_livekit_config_contains_no_secret_shaped_values():
    """Catches a credential re-added under any key name, not just `keys:`.

    Scans the PARSED scalar values, not the raw text. The file's own prose
    mentions LIVEKIT_API_KEY and friends, but those live in comments, which the
    YAML parser drops for us — so no comment stripping is needed, and none is
    done. An earlier line-based `split("#", 1)[0]` was unsound in the other
    direction: it also truncated quoted scalars, so a credential embedded after
    a `#` inside a quoted value (`field: "x#<43 base64url chars>"`) was silently
    removed before the regex ever saw it and the check passed. Parsing is what
    makes "comment" and "value" distinguishable at all.
    """
    parsed = yaml.safe_load(LIVEKIT_CONFIG.read_text()) or {}
    found = [t for value in _scalars(parsed) for t in SECRET_SHAPED.findall(value)]
    assert not found, (
        f"secret-shaped token(s) in services/livekit/config.yaml: "
        f"{[t[:4] + '...' for t in found]} — do not commit credentials here."
    )


def test_compose_supplies_livekit_keys_by_interpolation():
    """The container must still GET keys, and never from a literal in the file."""
    raw = COMPOSE.read_text()
    parsed = yaml.safe_load(raw)
    env = parsed["services"]["livekit"].get("environment")
    assert env, "livekit service lost its `environment:` block — it will not start"

    if isinstance(env, dict):
        value = env.get("LIVEKIT_KEYS")
    else:
        entries = [e for e in env if str(e).startswith("LIVEKIT_KEYS=")]
        assert entries, (
            "no LIVEKIT_KEYS entry. NOTE: an UNQUOTED `- LIVEKIT_KEYS=a: b` "
            "parses as a YAML mapping, not a string — quote it."
        )
        value = entries[0].split("=", 1)[1]

    assert value, "LIVEKIT_KEYS is empty"
    # Must be built from variables, never baked in.
    assert "${LIVEKIT_API_KEY" in value and "${LIVEKIT_API_SECRET" in value, (
        "LIVEKIT_KEYS must interpolate ${LIVEKIT_API_KEY}/${LIVEKIT_API_SECRET} "
        "from the untracked .env, not embed a literal credential."
    )
    # livekit-server yaml-unmarshals this string: the ": " separator is exact.
    assert re.search(r"\}: \$\{", value), (
        "LIVEKIT_KEYS must be exactly '<key>: <secret>' including the space; "
        "livekit-server rejects it otherwise."
    )
