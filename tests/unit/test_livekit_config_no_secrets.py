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

# One compose interpolation: `${NAME}` optionally followed by any of the six
# operators and a body. Kept as a fragment so the whole-value grammar below is
# built from the same definition the body check uses.
_INTERPOLATION = r"\$\{([A-Za-z_][A-Za-z0-9_]*)((?::?[-?+])[^}]*)?\}"

# The WHOLE value, anchored. `re.fullmatch` is the load-bearing part: a
# substring check ("is ${LIVEKIT_API_KEY somewhere in here") accepts anything
# around and between the two variables, including a literal credential appended
# after the closing brace. livekit-server yaml-unmarshals this string, so
# `<key>: <secret>` with that exact separator is the only valid shape anyway.
LIVEKIT_KEYS_GRAMMAR = re.compile(f"{_INTERPOLATION}: {_INTERPOLATION}")

# Exactly these two variables, in this order. Names matter: `${LIVEKIT_API_KEY_BACKUP:-}`
# contains the string "${LIVEKIT_API_KEY" and satisfies every shape rule while
# leaving the server keyless — Talk then dies silently at the next container start.
EXPECTED_LIVEKIT_VARS = ["LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]


def _interpolation_body(default):
    """The literal a compose operator would substitute, for any of the six forms."""
    if not default:
        return ""
    return default[2:] if default[:2] in (":-", ":?", ":+") else default[1:]


def parse_livekit_keys(value):
    """Parse LIVEKIT_KEYS against the exact grammar.

    Returns `[(var, default), ...]` for a conforming value, or None when the
    value does not match the grammar END TO END. Module-level so the negative
    controls below drive the same code the real assertion does — a control that
    exercises a private copy of the logic proves nothing about the guard.
    """
    match = LIVEKIT_KEYS_GRAMMAR.fullmatch(value)
    if match is None:
        return None
    var_1, default_1, var_2, default_2 = match.groups()
    return [(var_1, default_1 or ""), (var_2, default_2 or "")]


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
    # Nothing below is a relaxation of what this test used to assert. The old
    # substring pair ("${LIVEKIT_API_KEY" in value) and separator search
    # (`re.search(r"\}: \$\{")`) are both SUBSUMED: the grammar demands that exact
    # separator between two interpolations and nothing else anywhere in the
    # value, and the name check demands those two variables specifically. Every
    # value the old assertions accepted and the new ones reject is a value that
    # was wrong — see the negative controls at the bottom of this file.
    #
    # Must be built from variables, never baked in — and the WHOLE value must be
    # nothing but those two variables. A substring check ("${LIVEKIT_API_KEY is
    # in here somewhere") is satisfied while arbitrary text sits outside the
    # braces, so a literal appended after the closing `}` would ship a committed
    # credential that no scanner can recognise — the exact premise of #1644.
    parsed_vars = parse_livekit_keys(value)
    assert parsed_vars is not None, (
        f"LIVEKIT_KEYS ({value!r}) is not exactly "
        f"'${{LIVEKIT_API_KEY...}}: ${{LIVEKIT_API_SECRET...}}'. Any text outside "
        f"the two interpolations is a literal in a tracked file, and livekit-server "
        f"yaml-unmarshals this string so the ': ' separator is exact anyway."
    )
    # Referencing *a* variable is not the same as referencing the RIGHT one.
    # `${LIVEKIT_API_KEY_BACKUP:-}` passes every shape rule and leaves the server
    # with no keys at all; the failure surfaces as Talk dying at the next start.
    assert [var for var, _ in parsed_vars] == EXPECTED_LIVEKIT_VARS, (
        f"LIVEKIT_KEYS interpolates {[v for v, _ in parsed_vars]}, expected "
        f"{EXPECTED_LIVEKIT_VARS}. A near-miss name leaves livekit-server keyless "
        f"— see docs/knowledge/livekit-key-rotation.md."
    )
    # …and "interpolated" is not the same as "not baked in". Compose's default AND
    # replacement operators both put a LITERAL in the tracked file while still
    # referencing the variable, so the check above passes either way:
    #   ${VAR:-lit} / ${VAR-lit}   substitute `lit` when VAR is blank/unset
    #   ${VAR:+lit} / ${VAR+lit}   substitute `lit` when VAR IS set — i.e. on the
    #                              normal path, which is worse
    #   ${VAR:?msg} / ${VAR?msg}   msg is not a credential, but the rule is uniform
    # A LiveKit key has no vendor pattern for any scanner to catch, so all six
    # forms must carry an EMPTY body here.
    for var, default in parsed_vars:
        body = _interpolation_body(default)
        assert not body.strip(), (
            f"LIVEKIT_KEYS gives ${{{var}}} a non-empty interpolation default "
            f"({default!r}). A default is a literal: compose substitutes it whenever "
            f"the variable is unset, so this is a committed credential wearing an "
            f"interpolation costume. Leave it empty and let livekit-server refuse."
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


def test_interpolation_default_check_rejects_a_baked_literal():
    """NEGATIVE CONTROL: the default-is-a-literal check must actually reject one.

    `${VAR:-<secret>}` satisfies "the variable is referenced", so without this
    control the guard above would pass on the exact bypass it exists to close.

    Drives `parse_livekit_keys` — the same function the real assertion calls, not
    a local re-implementation of it. A control that exercises a private copy of
    the logic goes green while the shipped guard is broken.
    """

    def _bad_defaults(value):
        parsed = parse_livekit_keys(value)
        assert parsed is not None, f"grammar rejected {value!r} before the body check"
        return [var for var, default in parsed if _interpolation_body(default).strip()]

    secret = "A" * 43
    # Every operator Compose supports, not just the default ones. `:+`/`+` are the
    # nastiest: they substitute the literal when the variable IS set, i.e. on the
    # normal path, so a miss there ships the baked credential in production.
    for op in (":-", "-", ":+", "+", ":?", "?"):
        baked = "${LIVEKIT_API_KEY%szoe-abc123}: ${LIVEKIT_API_SECRET%s%s}" % (op, op, secret)
        assert _bad_defaults(baked) == ["LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"], (
            f"the interpolation check does not see a literal behind `{op}` — "
            f"docker compose supports it, so it is a live bypass"
        )
    for clean in (
        "${LIVEKIT_API_KEY:-}: ${LIVEKIT_API_SECRET:-}",
        "${LIVEKIT_API_KEY}: ${LIVEKIT_API_SECRET}",
    ):
        assert _bad_defaults(clean) == [], f"false positive on {clean!r}"


def test_grammar_rejects_anything_outside_the_two_interpolations():
    """NEGATIVE CONTROL: a literal OUTSIDE the braces must not parse.

    The guard used to inspect only the text INSIDE `${...}`, so a credential
    appended after the closing brace was never looked at — it sailed through
    every shape rule while committing key material a scanner cannot recognise,
    which is the entire premise of #1644. Measured against the pre-fix guard:
    `${LIVEKIT_API_KEY:-}: ${LIVEKIT_API_SECRET:-}<43 base64url chars>` passed
    all three of its assertions.
    """
    secret = "A" * 43
    for bad in (
        # trailing literal on the SECRET side — the dangerous one, and the shape
        # the old `\}: \$\{` separator search could not see at all
        "${LIVEKIT_API_KEY:-}: ${LIVEKIT_API_SECRET:-}" + secret,
        # leading literal before the key
        "zoe-abc123${LIVEKIT_API_KEY:-}: ${LIVEKIT_API_SECRET:-}",
        # a literal wedged between them
        "${LIVEKIT_API_KEY:-}: " + secret + " ${LIVEKIT_API_SECRET:-}",
        # a third variable smuggled onto the end
        "${LIVEKIT_API_KEY:-}: ${LIVEKIT_API_SECRET:-}, ${OTHER:-}",
        # fully baked, no interpolation at all
        f"zoe-abc123: {secret}",
        # wrong separator — livekit-server rejects it, so it must not pass here
        "${LIVEKIT_API_KEY:-}:${LIVEKIT_API_SECRET:-}",
    ):
        assert parse_livekit_keys(bad) is None, (
            f"the LIVEKIT_KEYS grammar accepts {bad!r} — text outside the two "
            f"interpolations is a literal in a tracked file"
        )

    assert parse_livekit_keys("${LIVEKIT_API_KEY:-}: ${LIVEKIT_API_SECRET:-}") == [
        ("LIVEKIT_API_KEY", ":-"),
        ("LIVEKIT_API_SECRET", ":-"),
    ], "the grammar rejects the form this repo actually ships"
    assert parse_livekit_keys("${LIVEKIT_API_KEY}: ${LIVEKIT_API_SECRET}") == [
        ("LIVEKIT_API_KEY", ""),
        ("LIVEKIT_API_SECRET", ""),
    ], "the grammar rejects the bare (operator-free) form"


def test_variable_names_must_be_exactly_the_livekit_pair():
    """NEGATIVE CONTROL: a near-miss variable name must not satisfy the guard.

    `${LIVEKIT_API_KEY_BACKUP:-}` CONTAINS the string `${LIVEKIT_API_KEY`, so the
    old substring check passed while compose interpolated a variable nobody sets
    — livekit-server then starts with no keys and Talk dies at the next container
    start, silently, with the tracked file looking correct.
    """
    for wrong in (
        "${LIVEKIT_API_KEY_BACKUP:-}: ${LIVEKIT_API_SECRET_BACKUP:-}",
        "${LIVEKIT_API_KEY_OLD:-}: ${LIVEKIT_API_SECRET:-}",
        "${LIVEKIT_API_SECRET:-}: ${LIVEKIT_API_KEY:-}",  # right names, swapped
        "${SOME_OTHER_KEY:-}: ${SOME_OTHER_SECRET:-}",
    ):
        parsed = parse_livekit_keys(wrong)
        assert parsed is not None, f"grammar rejected {wrong!r} before the name check"
        assert [var for var, _ in parsed] != EXPECTED_LIVEKIT_VARS, (
            f"{wrong!r} is accepted as the LiveKit key pair — a near-miss name "
            f"leaves livekit-server keyless"
        )


# ── .gitignore: credential SHAPES stay ignored, source stays trackable ──────
# Both directions have burned this repo. A blanket `*secret*` silently dropped
# THIS FILE (`git add` only warns), so the guard against a credential returning
# to services/livekit/config.yaml simply was not committed. Narrowing it to an
# extension allowlist fixed that and re-opened the other side: `client_secret.ini`,
# `db-password.csv` and `secret.properties` became trackable, and LiveKit keys have
# no vendor pattern for a content scanner to fall back on. The rule is now
# default-deny plus an explicit source/test/doc allowlist; these pin both halves.

_MUST_BE_IGNORED = (
    "client_secret.ini", "db-password.csv", "secret.properties", "secrets.yaml",
    "config.secret.json", "app-password.conf", "livekit-secret", "api_secret",
    "my_password", "passwords.txt", ".secret", "creds.password", "secret.p12",
)
_MUST_BE_TRACKABLE = (
    "tests/unit/test_livekit_config_no_secrets.py",
    "docs/knowledge/secret-rotation.md",
    "services/zoe-ui/dist/js/password-strength.js",
    "scripts/maintenance/rotate_secrets.sh",
)


def _is_ignored(path):
    """Does .gitignore match `path`? `--no-index` is LOAD-BEARING.

    Plain `git check-ignore` skips paths that are already TRACKED and reports
    them as not-ignored no matter what the patterns say. Without this flag the
    trackable-side assertions below are vacuous for the one path in the list that
    really exists in the repo — this file — i.e. the instrument would read green
    in exactly the situation it exists to catch. Confirmed: with the `!` unignore
    rules deleted, plain check-ignore still said "trackable" while `--no-index`
    correctly reported `.gitignore:51:*secret*`.
    """
    import subprocess

    return subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", "--", path],
        cwd=REPO_ROOT, capture_output=True,
    ).returncode == 0


@pytest.mark.parametrize("path", _MUST_BE_IGNORED)
def test_credential_shaped_names_are_ignored(path):
    """Any extension, or none — a credential artifact must not be committable."""
    assert _is_ignored(path), (
        f"{path} is trackable. Credential-shaped names must stay ignored "
        f"regardless of extension; see the .gitignore block above `*api_key*`."
    )


@pytest.mark.parametrize("path", _MUST_BE_TRACKABLE)
def test_source_and_doc_names_stay_trackable(path):
    """A guard test named after the thing it guards must still be committable.

    This file is the case in point: `git add` only WARNS on an ignored path, so a
    blanket rule removes the guard silently and nothing goes red.
    """
    assert not _is_ignored(path), (
        f"{path} is ignored by .gitignore. `git add` only warns, so a file like "
        f"this vanishes from a commit silently — add a `!` unignore for its "
        f"extension rather than leaving the blanket to eat it."
    )
