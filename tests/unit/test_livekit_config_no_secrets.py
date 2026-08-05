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
ENV_EXAMPLES = (
    REPO_ROOT / ".env.example",
    REPO_ROOT / "services" / "zoe-data" / ".env.example",
)

# A credential COMMENTED OUT rather than deleted — the usual way to "disable"
# a config block — is invisible to yaml.safe_load, so the parsed-value scan
# below cannot see it. Match the shape a LiveKit `keys:` entry actually has: an
# opaque id, a colon and whitespace, then a base64url secret running to end of
# line. Deliberately excludes `/` and `.`, so documentation paths
# (`docs/knowledge/livekit-key-rotation.md`) and URLs never match.
COMMENTED_CREDENTIAL = re.compile(
    r"^[ \t]*#.*?\b[A-Za-z0-9][A-Za-z0-9_-]{6,}:[ \t]+[A-Za-z0-9_-]{32,}[ \t]*$",
    re.MULTILINE,
)

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


def test_commented_out_credentials_are_caught_too():
    """A credential commented out is still a credential in a public repo.

    `yaml.safe_load` drops comments, so the parsed-scalar scan above is blind to
    `#   zoe-abc: <secret>` — and commenting a block out instead of deleting it
    is the ordinary way people disable config. The file's own prose survives
    this matcher because it requires a `<id>: <32+ base64url>` pair running to
    end of line, with `/` and `.` excluded so doc paths cannot match.
    """
    raw = LIVEKIT_CONFIG.read_text()
    hits = COMMENTED_CREDENTIAL.findall(raw)
    assert not hits, (
        f"credential-shaped text inside a comment in services/livekit/config.yaml: "
        f"{[h.strip()[:12] + '...' for h in hits]} — delete it, do not comment it "
        f"out; git history on a public repo keeps it either way."
    )


def test_commented_credential_matcher_actually_matches():
    """NEGATIVE CONTROL for the scan above — and for its false-positive edges.

    A comment scanner that matches nothing is indistinguishable from a clean
    file. Feed it the exact line this repo shipped (with the secret replaced by
    a same-shape stand-in) and require a hit, then feed it the prose that must
    NOT trip it.
    """
    disabled = "keys:\n#  zoe-k5Sq6QANfemQ1ash: " + "A" * 43 + "\n"
    assert COMMENTED_CREDENTIAL.search(disabled), (
        "the commented-credential matcher no longer matches a commented-out "
        "`keys:` entry — the scan above is guarding nothing"
    )
    for benign in (
        "# Rotation runbook: docs/knowledge/livekit-key-rotation.md\n",
        "# sourced from the untracked repo-root .env (LIVEKIT_API_KEY / LIVEKIT_API_SECRET).\n",
        "# livekit-server v1.9.3: `--keys` / $LIVEKIT_KEYS is applied AFTER this file\n",
        "# see https://docs.livekit.io/realtime/server/configuration/for/details\n",
    ):
        assert not COMMENTED_CREDENTIAL.search(benign), (
            f"false positive on documentation prose: {benign.strip()!r}"
        )


def test_livekit_keys_is_not_a_required_value_expression():
    """One optional service's credential must never abort compose for ALL services.

    Compose interpolates the whole model before it selects services, so
    `${LIVEKIT_API_KEY:?...}` in the livekit block aborts EVERY compose command
    on a box without the pair. Measured on this repo's file: `docker compose
    config zoe-auth` with no LiveKit vars exits 15 with "required variable
    LIVEKIT_API_KEY is missing a value". That breaks `scripts/setup/
    install-jetson.sh` on a fresh box (it copies .env.example, then brings up the
    non-LiveKit spine) and deploy.yml's `docker compose up -d zoe-auth`.

    Empty is still a loud failure, just correctly scoped: livekit-server refuses
    to serve (`Could not parse keys, it needs to be exactly, "key: secret"`),
    the same class of refusal as supplying no keys at all.
    """
    raw = COMPOSE.read_text()
    parsed = yaml.safe_load(raw)
    env = parsed["services"]["livekit"]["environment"]
    entries = env if isinstance(env, list) else [f"{k}={v}" for k, v in env.items()]
    value = [e for e in entries if str(e).startswith("LIVEKIT_KEYS=")][0].split("=", 1)[1]

    assert ":?" not in value and not re.search(r"\$\{[A-Za-z_][A-Za-z0-9_]*\?", value), (
        "LIVEKIT_KEYS uses a required-value interpolation (`${VAR:?...}` or "
        "`${VAR?...}`). Compose evaluates it for every command regardless of the "
        "services selected, so a box without the pair cannot start ANY service. "
        "Use `${VAR:-}` and let livekit-server refuse on its own."
    )


@pytest.mark.parametrize("path", ENV_EXAMPLES)
def test_env_examples_document_the_livekit_pair(path):
    """A fresh install must be TOLD the pair exists, in both files that need it.

    The compose interpolation no longer fails loudly when they are missing (see
    above), so the example files are what stops a missing pair from being a
    silent mystery. They must also stay blank — an example file with a working
    default credential is how default credentials reach production.
    """
    text = path.read_text()
    for name in ("LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"):
        match = re.search(rf"^{name}=(.*)$", text, re.MULTILINE)
        assert match, (
            f"{path.relative_to(REPO_ROOT)} does not document {name}; a fresh "
            f"install has no way to know LiveKit needs it. See "
            f"docs/knowledge/livekit-key-rotation.md."
        )
        assert match.group(1).strip() == "", (
            f"{path.relative_to(REPO_ROOT)} ships a non-empty {name} — an example "
            f"file must never carry a usable credential."
        )
    assert "livekit-key-rotation.md" in text, (
        f"{path.relative_to(REPO_ROOT)} should point at the rotation runbook"
    )
