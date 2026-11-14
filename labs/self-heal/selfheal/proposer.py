"""The engine adapter protocol: `propose(fault_packet) -> patch`.

A subscription engine (Codex CLI, Claude Code CLI, a local model) is an ADAPTER behind one narrow call.
It gets a FAULT PACKET and returns a PATCH; it never gets the repo, a worktree, the state dir or the
environment, and it never writes anything itself: the loop applies the patch, after checking it.

  packet  - the ticket ids, the failing test's node id and (redacted, capped) output, the text of the
            FILES IN SCOPE (the reproducing test file and the tracked modules it imports), the path
            allow-list, the budgets, the attempt number and the last refusal REASON (a closed token).
            File text comes from the BASE COMMIT (`git show`), never from disk: an untracked or ignored
            file (.env, data, state) cannot enter. A file on the forbidden / secret-path lists, a pinned
            file, a symlink, a binary or a file with a secret-shaped string is WITHHELD (name counted
            only). The failure text comes from a sandboxed re-run at the base commit (no home, no state
            dir, no network) and then passes `redact`.
  patch   - a git-style unified diff. Only text edits to `allow_edit` paths (the in-scope non-test
            files) and NEW files matching `allow_new` (a new test next to the reproduction). No rename,
            copy, delete, mode change, symlink or binary section, no path outside the allow-list.
  budgets - wall clock is measured by the CALLER (a late answer is refused, the call abandoned); turns
            and tokens are compared as the engine REPORTS them (unreported = recorded as unreported,
            never trusted as zero); patch size is capped in bytes and by the diff-line cap.

Adapters here: `ScriptedProposer` (the fake for tests) and `DryRunProposer` (prints the command line a
named CLI WOULD run; executes nothing). No real engine adapter is built or run in this lab.
"""
from __future__ import annotations

import ast
import difflib
import fnmatch
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field

from . import gitops
from .config import MAX_DIFF_LINES, MAX_ENGINE_TOKENS, MAX_ENGINE_TURNS, Config
from .engines import ENV_ALLOW, ClaudeEngine, CodexEngine, EngineResult
from .safety import Refused, forbidden_hits, is_test_infra
from .sandbox import TMP_NAME, Sandbox
from .supervisor import is_pinned_name

PACKET_VERSION = 1
MAX_OUTPUT_CHARS, MAX_FILE_BYTES, MAX_FILES, MAX_PATCH_BYTES = 4000, 40_000, 8, 64_000
# Never sent whatever the commit holds (fnmatch on the repo-relative path).
SECRET_PATHS = ("*.pem", "*.key", "*.p12", "*.pfx", "*secret*", "*credential*", "*token*", "*.db", "*.sqlite*",
                "*.jsonl", "*.csv", "data/*", "*/data/*", "backups/*", "homeassistant/*")
# The engine's refusal / failure reasons (all single tokens; the loop's closed list includes them).
REASONS = ("patch_empty", "patch_oversized", "patch_binary", "patch_malformed", "patch_rename", "patch_delete",
           "patch_mode", "patch_symlink", "patch_path_escape", "patch_outside_allowlist", "patch_does_not_apply",
           "diff_cap", "budget_time", "budget_turns", "budget_tokens", "engine_error", "engine_dry_run",
           "packet_empty")

_SECRET = [
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S)),
    ("token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_\w{20,}|sk-[A-Za-z0-9_-]{20,}|"
                         r"xox[abprs]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|"
                         r"\d{8,10}:[A-Za-z0-9_-]{30,})")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("url_credential", re.compile(r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)")),
    ("bearer", re.compile(r"(?i)(?<=bearer )[A-Za-z0-9._~+/=-]{12,}")),
]
_PERSONAL = [   # failure text only: over-redaction is the safe direction there
    ("secret_value", re.compile(r"(?i)\b(\w*(?:password|passwd|secret|token|api_?key|cookie|authorization)\w*\s*[=:]\s*)"
                                r"(\"[^\"]*\"|'[^']*'|\S+)")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")),
    ("coordinate", re.compile(r"-?\b\d{1,3}\.\d{4,}\s*,\s*-?\d{1,3}\.\d{4,}\b")),
    ("ip", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("number", re.compile(r"(?<![\w.])\+?\d[\d ()-]{7,}\d(?![\w.])")),
    ("home", re.compile(r"/(?:home|Users|root)/[^/\s:'\"]*")),
]


def redact(text: str, *, personal: bool = True) -> tuple[str, dict]:
    """(redacted text, {class: count}). Secret SHAPES always; with `personal`, also values assigned to
    secret-named keys, e-mail addresses, coordinates, IPs, long digit runs and home paths.
    Pattern-based: it cannot recognise free-text household detail. That is kept out structurally
    (commit-only file text, sandboxed failure text), not by this function."""
    counts: dict = {}
    for name, rx in _SECRET + (_PERSONAL if personal else []):
        repl = (lambda m, n=name: f"{m.group(1)}<redacted:{n}>") if name == "secret_value" else f"<redacted:{name}>"
        text, n = rx.subn(repl, text)
        if n:
            counts[name] = n
    return text, counts


@dataclass(frozen=True)
class Budget:
    seconds: int
    turns: int
    tokens: int
    patch_bytes: int = MAX_PATCH_BYTES

    @classmethod
    def of(cls, cfg: Config) -> "Budget":
        return cls(cfg.engine_timeout_s, min(cfg.engine_max_turns, MAX_ENGINE_TURNS),
                   min(cfg.engine_max_tokens, MAX_ENGINE_TOKENS))


@dataclass
class FaultPacket:
    version: int
    ticket: str
    fault_class: str
    test: str                 # the reproduction's pytest node id
    repro: str                # how to reproduce, relative ("python -m pytest -q <node>")
    base_sha: str
    failure: str              # redacted, capped test output
    files: dict               # {repo-relative path: text at the base commit}
    allow_edit: list          # exact paths the patch may edit
    allow_new: list           # fnmatch patterns a NEW file may match
    budget: dict
    attempt: int = 1
    last_refusal: str = ""    # the previous round's refusal reason: a token from a closed list, never text
    withheld: int = 0         # in-scope files NOT sent (forbidden / secret path / pinned / binary / secret-shaped)
    redactions: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


@dataclass
class Usage:
    turns: int | None = None      # None = the engine did not report it
    tokens: int | None = None


@dataclass
class Proposal:
    patch: str = ""
    usage: Usage = field(default_factory=Usage)
    would_run: list | None = None   # dry-run adapters only: the argv lists, nothing executed


# --- the packet -------------------------------------------------------------------------------

def _tracked(wt: str, sha: str) -> dict:
    out = {}
    for entry in filter(None, gitops.run(["git", "-C", wt, "ls-tree", "-r", "-z", sha]).stdout.split("\0")):
        meta, path = entry.split("\t", 1)
        out[path] = meta.split()[0]   # the mode
    return out


def _imports(text: str) -> list:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    mods = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            mods += [a.name for a in n.names]
        elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
            mods += [n.module, *(f"{n.module}.{a.name}" for a in n.names)]
    return list(dict.fromkeys(mods))


def _show(wt: str, sha: str, path: str) -> str | None:
    try:
        text = gitops.run(["git", "-C", wt, "show", f"{sha}:{path}"]).stdout
    except (UnicodeDecodeError, gitops.GitError):
        return None
    return None if "\0" in text or len(text.encode()) > MAX_FILE_BYTES else text


def sendable(cfg: Config, path: str, mode: str) -> bool:
    return (mode == "100644" or mode == "100755") and not forbidden_hits(cfg, [path]) \
        and not any(fnmatch.fnmatch(path, p) for p in SECRET_PATHS) and not is_pinned_name(path)


def build_packet(cfg: Config, ticket, wt: str, *, attempt: int = 1, last_refusal: str = "") -> FaultPacket:
    """Everything the engine may see. `wt` is a worktree AT the base commit (only read here, plus one
    sandboxed re-run of the reproduction for its output)."""
    sha, tracked = ticket.base_sha, _tracked(wt, ticket.base_sha)
    scope = [f for f in ticket.repro_files if f in tracked]
    for f in list(scope):
        for mod in _imports(_show(wt, sha, f) or ""):
            rel = mod.replace(".", "/")
            hits = [p for p in tracked if any(p == s or p.endswith("/" + s) for s in (f"{rel}.py", f"{rel}/__init__.py"))]
            if len(hits) == 1 and hits[0] not in scope:
                scope.append(hits[0])
    files, withheld = {}, 0
    for path in scope[:MAX_FILES]:
        text = _show(wt, sha, path) if sendable(cfg, path, tracked[path]) else None
        if text is None or redact(text, personal=False)[1]:
            withheld += 1   # secret-shaped text is never "cleaned and sent": a mangled file cannot be patched
        else:
            files[path] = text
    withheld += max(0, len(scope) - MAX_FILES)
    rc, out = Sandbox(cfg).run(list(ticket.repro_cmd), wt, cfg.gate_timeout_s)
    out = (out if rc is not None else "").replace(os.path.realpath(wt), ".").replace(wt, ".")
    failure, counts = redact(out)
    if len(failure) > MAX_OUTPUT_CHARS:
        failure = failure[:MAX_OUTPUT_CHARS * 3 // 4] + "\n<...cut...>\n" + failure[-MAX_OUTPUT_CHARS // 4:]
    dirs = sorted({os.path.dirname(f) for f in ticket.repro_files})
    return FaultPacket(
        version=PACKET_VERSION, ticket=ticket.id, fault_class=ticket.fault_class,
        test=ticket.evidence.get("nodeid", ""), base_sha=sha, failure=failure, files=files,
        repro="python " + " ".join(ticket.repro_cmd[1:]),
        allow_edit=sorted(p for p in files if not is_test_infra(p)),
        allow_new=[(d + "/" if d else "") + "test_*.py" for d in dirs],
        budget=asdict(Budget.of(cfg)), attempt=attempt, last_refusal=last_refusal if re.fullmatch(r"[a-z_]{1,40}", last_refusal) else "",
        withheld=withheld, redactions=counts)


def packet_prompt(p: FaultPacket) -> str:
    """The text a CLI adapter would hand its engine (the engine works in a dir holding ONLY `p.files`)."""
    return "\n".join([
        "Fix ONE fault. This directory holds only the files you may read.",
        f"Failing test: {p.test}", f"Reproduce: {p.repro}", "Test output (redacted):", p.failure,
        f"You may edit only: {', '.join(p.allow_edit)}. You may add a new test matching: {', '.join(p.allow_new)}.",
        "Fix the cause; the original test is restored before the fix is verified. Do not rename, delete or",
        "chmod anything, create no symlink, run no git or network command.",
        f"Attempt {p.attempt}." + (f" The previous attempt was refused: {p.last_refusal}." if p.last_refusal else ""),
    ])


# --- the patch --------------------------------------------------------------------------------

def make_patch(old: dict, new: dict) -> str:
    """A git-style unified diff from {path: text} before / after (what an adapter builds from the
    packet dir after its engine ran; also how the scripted fake writes its canned patches)."""
    out = []
    for path in sorted(set(old) | set(new)):
        a, b = old.get(path), new.get(path)
        if a == b or b is None:
            continue
        out.append(f"diff --git a/{path} b/{path}\n" + ("new file mode 100644\n" if a is None else ""))
        out += difflib.unified_diff((a or "").splitlines(True), b.splitlines(True),
                                    "/dev/null" if a is None else f"a/{path}", f"b/{path}")
        if not out[-1].endswith("\n"):
            out.append("\n\\ No newline at end of file\n")
    return "".join(out)


_HUNK = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
_HEAD = re.compile(r"^diff --git a/(\S+) b/(\S+)$")


def parse_patch(patch: str) -> list:
    """[{path, new, lines}] or Refused. Hunk bodies are COUNTED, so a body line that looks like a
    header is body, and anything between hunks that is not a known, harmless header is refused."""
    files, cur, old, new = [], None, 0, 0
    for line in patch.split("\n"):
        if old > 0 or new > 0:
            c = line[:1]
            if c == "\\":
                continue
            if c in ("+", "-"):
                cur["lines"] += 1
            elif c not in (" ", ""):
                raise Refused("patch_malformed", "hunk")
            old, new = old - (c != "+"), new - (c != "-")
            if old < 0 or new < 0:
                raise Refused("patch_malformed", "hunk")
            continue
        m = _HEAD.match(line)
        if m:
            if m[1] != m[2]:
                raise Refused("patch_rename", "")
            cur = {"path": m[1], "new": False, "lines": 0}
            files.append(cur)
        elif line == "" or line.startswith("\\"):
            continue
        elif cur is None:
            raise Refused("patch_malformed", "preamble")
        elif line.startswith(("rename ", "copy ", "similarity ", "dissimilarity ")):
            raise Refused("patch_rename", "")
        elif line.startswith(("GIT binary patch", "Binary files ")):
            raise Refused("patch_binary", "")
        elif line.startswith("deleted file mode") or line == "+++ /dev/null":
            raise Refused("patch_delete", "")
        elif line.startswith(("old mode", "new mode")):
            raise Refused("patch_mode", "")
        elif line.startswith(("new file mode ", "index ")):
            mode = line.split()[-1] if line.startswith("new") or len(line.split()) == 3 else "100644"
            if mode != "100644":
                raise Refused("patch_symlink" if mode == "120000" else "patch_mode", "")
            cur["new"] = cur["new"] or line.startswith("new")
        elif line.startswith("--- "):
            if line[4:].split("\t")[0] != ("/dev/null" if cur["new"] else "a/" + cur["path"]):
                raise Refused("patch_malformed", "old_path")
        elif line.startswith("+++ "):
            if line[4:].split("\t")[0] != "b/" + cur["path"]:
                raise Refused("patch_malformed", "new_path")
        elif _HUNK.match(line):
            g = _HUNK.match(line).groups()
            old, new = int(g[0] or 1), int(g[1] or 1)
        else:
            raise Refused("patch_malformed", "header")
    if old or new:
        raise Refused("patch_malformed", "truncated")
    return files


def check_patch(patch, packet: FaultPacket, cfg: Config) -> list:
    """The allowed output, or Refused(<reason>). Returns the sorted paths the patch touches."""
    if not isinstance(patch, str) or not patch.strip():
        raise Refused("patch_empty", "")
    if len(patch.encode(errors="replace")) > min(packet.budget.get("patch_bytes", 0), MAX_PATCH_BYTES):
        raise Refused("patch_oversized", "")
    if "\0" in patch:
        raise Refused("patch_binary", "")
    files = parse_patch(patch)
    if not files or not any(f["lines"] for f in files):
        raise Refused("patch_empty", "")
    for f in files:
        p = f["path"]
        parts = p.split("/")
        if p.startswith("/") or ".." in parts or ".git" in parts or "" in parts or parts[0] == TMP_NAME:
            raise Refused("patch_path_escape", "")
        ok = (any(fnmatch.fnmatch(p, pat) for pat in packet.allow_new) and p not in packet.files) if f["new"] \
            else p in packet.allow_edit
        if not ok or forbidden_hits(cfg, [p]) or is_pinned_name(p):
            raise Refused("patch_outside_allowlist", "")
    if len({f["path"] for f in files}) != len(files):
        raise Refused("patch_malformed", "duplicate_file")
    if sum(f["lines"] for f in files) > min(cfg.diff_line_cap, MAX_DIFF_LINES):
        raise Refused("diff_cap", "")
    return sorted(f["path"] for f in files)


def apply_patch(wt: str, patch: str) -> None:
    """Working tree only (the runner alone stages). `git apply` itself refuses a path outside the tree
    or beyond a symlink; the runner's `inspect_worktree` + `check_diff` then re-measure the real tree."""
    fd, tmp = tempfile.mkstemp(prefix="selfheal-patch-", suffix=".diff")
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(patch)
        for mode in (["--check"], []):
            if gitops.run(["git", "-C", wt, "apply", "--whitespace=nowarn", *mode, tmp], check=False).returncode != 0:
                raise Refused("patch_does_not_apply", "")
    finally:
        os.unlink(tmp)


# --- adapters ---------------------------------------------------------------------------------

class ScriptedProposer:
    """The FAKE engine: answers from a script, one entry per call (the last repeats). An entry is a
    Proposal, a patch string, or a callable(packet) returning either."""
    name = "fake"

    def __init__(self, *script):
        self.script, self.packets = list(script), []

    def propose(self, packet: FaultPacket) -> Proposal:
        self.packets.append(packet)
        step = self.script[min(len(self.packets), len(self.script)) - 1]
        out = step(packet) if callable(step) else step
        return out if isinstance(out, Proposal) else Proposal(patch=out)


class DryRunProposer:
    """Prints (returns) the command lines a named CLI adapter WOULD run; executes nothing, opens no
    socket. The engine would work in a fresh directory holding only the packet's files; the adapter
    would then diff that directory against the packet to build the patch."""
    CLIS = {"codex": CodexEngine, "claude": ClaudeEngine}
    WORKDIR = "<packet-dir>"

    def __init__(self, cfg: Config, cli: str):
        if cli not in self.CLIS:
            raise ValueError(f"dry-run knows {sorted(self.CLIS)}, not {cli!r}")
        self.cfg, self.cli, self.name = cfg, cli, f"dryrun-{cli}"

    def propose(self, packet: FaultPacket) -> Proposal:
        prompt = packet_prompt(packet)
        shown = f"<prompt chars={len(prompt)} sha256={hashlib.sha256(prompt.encode()).hexdigest()[:16]}>"
        argv = self.CLIS[self.cli](self.cfg).argv(self.WORKDIR, shown)   # the built-in, asserted flag set
        return Proposal(would_run=[
            {"step": "materialize", "argv": ["write", f"{len(packet.files)} packet files", self.WORKDIR]},
            {"step": "init", "argv": ["git", "init", "-q", self.WORKDIR]},
            {"step": "engine", "argv": argv, "cwd": self.WORKDIR, "timeout_s": packet.budget["seconds"],
             "env_names": sorted(ENV_ALLOW)},
            {"step": "collect", "argv": ["git", "-C", self.WORKDIR, "diff", "--no-renames"]}])


def _call(proposer, packet: FaultPacket, seconds: int) -> Proposal:
    """`propose` under the caller's clock. A call that has not answered by the deadline is abandoned
    (daemon thread) and refused; a CLI adapter must also kill its own process group."""
    box: dict = {}

    def work():
        try:
            box["out"] = proposer.propose(packet)
        except Exception as e:   # noqa: BLE001 - reported by class only
            box["err"] = type(e).__name__
    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(seconds)
    if t.is_alive():
        raise Refused("budget_time", "")
    if "out" not in box or not isinstance(box["out"], Proposal):
        raise Refused("engine_error", box.get("err", "bad_return"))
    return box["out"]


class ProposerEngine:
    """Bridges a proposer into the runner's engine seam (`run(ticket, worktree)`): packet -> propose ->
    budgets -> patch check -> apply. `last` holds the reason and the reported usage (numbers only)."""

    def __init__(self, cfg: Config, proposer):
        self.cfg, self.proposer, self.name = cfg, proposer, proposer.name
        self.attempt, self.last_refusal, self.last = 1, "", {}

    def run(self, ticket, wt: str) -> EngineResult:
        t0 = time.monotonic()
        self.last = {"reason": "", "turns": None, "tokens": None, "would_run": None, "files": []}
        try:
            packet = build_packet(self.cfg, ticket, wt, attempt=self.attempt, last_refusal=self.last_refusal)
            if not packet.allow_edit:
                raise Refused("packet_empty", "")
            got = _call(self.proposer, packet, packet.budget["seconds"])
            self.last.update(turns=got.usage.turns, tokens=got.usage.tokens, would_run=got.would_run)
            if got.would_run is not None:
                raise Refused("engine_dry_run", "")
            for name in ("turns", "tokens"):
                v = getattr(got.usage, name)
                if v is not None and (not isinstance(v, int) or v > packet.budget[name]):
                    raise Refused(f"budget_{name}", "")
            self.last["files"] = check_patch(got.patch, packet, self.cfg)
            apply_patch(wt, got.patch)
        except Refused as e:
            self.last["reason"] = e.rule
            return EngineResult(False, "rc=refused", time.monotonic() - t0, reason=e.rule)
        return EngineResult(True, "rc=0", time.monotonic() - t0)
