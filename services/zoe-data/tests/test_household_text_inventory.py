"""The household-text inventory (ROADMAP A5-A7): every place household words can be written is declared.

``household_text.py`` is the registry; this test holds the code to it. It FAILS when:

  * a function that handles text-like values (``text``, ``utterance``, ``transcript``, ``message``, ``reply``, ``prompt``, ...)
    writes a file (``open(.., "w"/"a"/"x")``, ``.write_text``, ``.write_bytes``, ``json.dump``, ``wave.open(.., "wb")``) and that
    ``relpath::function`` is in neither ``WRITE_SITES`` (mapped to a store) nor ``NOT_HOUSEHOLD`` (reviewed: not household text);
  * a migration / runtime ``CREATE TABLE`` has a text-like column and the table is in no inventory (``forget_reach.TABLES``,
    or ``NO_TEXT_TABLES`` below with a reason);
  * a store of words in ``forget_reach`` is missing here, an entry is incomplete, a ``gap`` is not ledgered in open-problems.md,
    an allowlist pattern matches nothing any more, or the docs page drifts from the registry.

What it cannot see is stated in docs/knowledge/household-text-inventory.md. Pure parsing (``ci_safe``): no database, no network.
Break-the-fix controls are in-file (``test_the_detector_catches_*``).
"""
from __future__ import annotations

import ast
import fnmatch
import os
import re
from pathlib import Path

import pytest

import forget_reach as fr
import household_text as ht

pytestmark = pytest.mark.ci_safe

SVC = Path(__file__).resolve().parents[1]
REPO = SVC.parents[1]
SCAN_ROOTS = ("services/zoe-data", "scripts")
_SKIP_DIRS = {"tests", "test", "node_modules", "__pycache__", "venv", ".venv", "data", "models", "archive",
              "logs", "backups", "backup", "results", "scratch", "captures", "artifacts", "checkpoints", "recordings"}

# identifier tokens (split on "_") that mark a value as text a person said or wrote, or Zoe's reply to it
TEXTY = re.compile(r"(?:^|_)(text|utt|utterance|utterances|transcript|transcripts|message|messages|reply|replies|prompt|words|said"
                   r"|turn|turns|fact|facts|hyp|hyps|hypothesis|stt|heard|asr)(?:$|_)", re.I)


def _resolve(n: ast.AST, aliases: dict[str, ast.AST], seen=frozenset()) -> ast.AST:
    if isinstance(n, ast.Name) and n.id in aliases and n.id not in seen:
        return _resolve(aliases[n.id], aliases, seen | {n.id})
    if isinstance(n, ast.Attribute):
        return ast.Attribute(value=_resolve(n.value, aliases, seen), attr=n.attr, ctx=ast.Load())
    return n


def _is_write(n: ast.AST, aliases=None) -> bool:
    if not isinstance(n, ast.Call):
        return False
    aliases = aliases or {}
    f = _resolve(n.func, aliases)
    if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) and (f.value.id, f.attr) == ("os", "open"):
        flags = n.args[1] if len(n.args) > 1 else next((k.value for k in n.keywords if k.arg == "flags"), None)
        def writable(flags, seen=frozenset()):
            if flags is None:
                return False
            if isinstance(flags, ast.Name) and flags.id in seen:
                return False
            if isinstance(flags, ast.Name) and flags.id in aliases and flags.id not in seen:
                return writable(aliases[flags.id], seen | {flags.id})
            flags = _resolve(flags, aliases)
            if isinstance(flags, ast.BinOp) and isinstance(flags.op, ast.BitOr):
                return writable(flags.left, seen) or writable(flags.right, seen)
            if isinstance(flags, ast.Constant) and isinstance(flags.value, int):
                return bool(flags.value & 3)  # POSIX O_WRONLY=1, O_RDWR=2
            return (isinstance(flags, ast.Attribute) and isinstance(flags.value, ast.Name)
                    and flags.value.id == "os" and flags.attr in {"O_WRONLY", "O_RDWR", "O_APPEND", "O_CREAT", "O_TRUNC"})
        return writable(flags)
    if (isinstance(f, ast.Name) and f.id == "open") or (isinstance(f, ast.Attribute) and f.attr == "open"):
        # Builtin/module open(file, mode); bound Path.open(mode). Unknown receivers
        # are conservatively checked at both positions (no runtime type guessing).
        modes = [k.value for k in n.keywords if k.arg == "mode"]
        modes += n.args[:2] if isinstance(f, ast.Attribute) else n.args[1:2]
        return any(isinstance(m := _resolve(mode, aliases), ast.Constant) and isinstance(m.value, str)
                   and re.fullmatch(r"(?:[rwax](?:[bt]\+?|\+[bt]?)?|[wax][|:](?:gz|bz2|xz)?)", m.value)
                   and any(c in m.value for c in "wax+") for mode in modes)
    if isinstance(f, ast.Attribute) and f.attr in ("write_text", "write_bytes"):
        return True
    if isinstance(f, ast.Attribute) and f.attr in ("write", "writelines", "writeframes"):
        return _is_write(_resolve(f.value, aliases), aliases)
    return isinstance(f, ast.Attribute) and f.attr == "dump" and isinstance(f.value, ast.Name) and f.value.id == "json"


_IO_NAMES = frozenset({"write_text", "read_text"})          # the file API itself is not a text value


def _texty(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute) and n.attr in _IO_NAMES:
            continue
        name = (n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else n.arg if isinstance(n, ast.arg)
                else n.arg if isinstance(n, ast.keyword) and not isinstance(n.value, ast.Constant) else None)   # not subprocess text=True
        if name and TEXTY.search(name):
            return True
        # a short space-free string: a dict key, a record field (rec["utt_text"]) or a file name ("x.texts.jsonl"); prose
        # (docstrings, messages) has spaces and is not judged
        if (isinstance(n, ast.Constant) and isinstance(n.value, str) and 0 < len(n.value) <= 60 and " " not in n.value
                and any(TEXTY.search(tok) for tok in re.split(r"[^A-Za-z0-9_]+", n.value) if tok)):
            return True
    return False


def write_sites(source: str, rel: str) -> set[str]:
    """``rel::function`` for every file write whose INNERMOST enclosing function (or the module) handles a text-like value."""
    tree = ast.parse(source)
    out: set[str] = set()

    def bindings(scope, inherited):
        aliases = dict(inherited)
        def collect(node):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    continue
                if isinstance(child, ast.Import):
                    for a in child.names:
                        aliases[a.asname or a.name] = ast.Name(id=a.name)
                elif isinstance(child, ast.ImportFrom):
                    for a in child.names:
                        aliases[a.asname or a.name] = ast.Attribute(value=ast.Name(id=child.module or ""), attr=a.name)
                elif isinstance(child, (ast.Assign, ast.AnnAssign)):
                    targets = child.targets if isinstance(child, ast.Assign) else [child.target]
                    for target in targets:
                        if isinstance(target, ast.Name) and child.value is not None:
                            aliases[target.id] = child.value
                collect(child)
        collect(scope)
        return aliases

    def visit(node: ast.AST, scope: ast.AST, name: str, aliases) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                local = {k: v for k, v in aliases.items() if k not in {a.arg for a in ast.walk(child.args) if isinstance(a, ast.arg)}}
                visit(child, child, child.name, bindings(child, local))
                continue
            if _is_write(child, aliases) and _texty(scope):
                out.add(f"{rel}::{name}")
            visit(child, scope, name, aliases)

    visit(tree, tree, "<module>", bindings(tree, {}))
    return out


def _py_files() -> list[Path]:
    out = []
    for base in SCAN_ROOTS:
        for root, dirs, files in os.walk(REPO / base, followlinks=False):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and not (Path(root) / d).is_symlink()]
            out.extend(Path(root) / f for f in files if f.endswith(".py") and not f.startswith("test_")
                       and not (Path(root) / f).is_symlink())
    return out


@pytest.fixture(scope="module")
def all_sites() -> set[str]:
    sites: set[str] = set()
    for p in _py_files():
        sites |= write_sites(p.read_text(encoding="utf-8", errors="replace"), p.relative_to(REPO).as_posix())
    return sites


# ── 1. the write-site detector ────────────────────────────────────────────────────────────────

def test_the_scan_sees_the_known_writers(all_sites):
    """Sanity control: a scan that silently found nothing would make the coverage test vacuous."""
    assert len(all_sites) >= 50
    for known in ("services/zoe-data/semantic_router.py::_append_shadow_line", "services/zoe-data/routers/telegram_media.py::_maybe_capture",
                  "services/zoe-data/zoe_agent.py::_log_feedback_triple"):
        assert known in all_sites


def test_every_text_write_site_is_declared(all_sites):
    unknown = sorted(s for s in all_sites if ht.classify(s) is None)
    assert not unknown, (
        f"code writes a file from a function handling text and the site is not in the household-text inventory: {unknown}. "
        "Map it to a store in services/zoe-data/household_text.py WRITE_SITES (add the store if it is new, with its forget reach), "
        "or, if it never holds household words, add it to NOT_HOUSEHOLD with the reason")


def test_no_allowlist_pattern_is_stale(all_sites):
    stale = [p for p in (*ht.WRITE_SITES, *ht.NOT_HOUSEHOLD) if not any(fnmatch.fnmatchcase(s, p) for s in all_sites)]
    assert not stale, f"allowlist entries that match no write site any more (remove them): {stale}"


def test_every_mapped_site_names_a_registered_store():
    assert {k for k in ht.WRITE_SITES.values()} <= set(ht.BY_KEY)


def test_inventory_names_stores_without_importing_runtime_readers():
    """The Omi boundary scans allow this metadata module, not new runtime access."""
    tree = ast.parse((SVC / "household_text.py").read_text())
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.add(node.module)
    assert imports <= {"__future__", "fnmatch", "dataclasses", "pathlib", "typing", "forget_reach", "sys"}
    assert not any(isinstance(n, ast.Name) and n.id in {"__import__", "eval", "exec", "get_db", "db_pool"}
                   for n in ast.walk(tree))


def test_the_detector_catches_a_new_text_sink():
    """Break-the-fix control: a new function that appends what someone said to a file is caught and is undeclared."""
    src = ('import json\n'
           'def keep_diary(utterance, path):\n'
           '    with open(path, "a") as fh:\n'
           '        fh.write(json.dumps({"said": utterance}))\n'
           'def save_cfg(cfg, path):\n'
           '    path.write_text(json.dumps(cfg))\n'
           'def read_only(text, path):\n'
           '    return open(path, "r").read()\n')
    got = write_sites(src, "services/zoe-data/diary.py")
    assert got == {"services/zoe-data/diary.py::keep_diary"}          # the config writer and the reader are not text sinks
    assert ht.classify("services/zoe-data/diary.py::keep_diary") is None


@pytest.mark.parametrize("mode", ["w", "a", "x", "w+", "wb", "ab", "r+"])
@pytest.mark.parametrize("opener", ["path.open({mode})", "path.open(mode={mode})",
                                    "open(path, {mode})", "open(file=path, mode={mode})",
                                    "writer = path.open; writer({mode})",
                                    "writer = open; writer(path, {mode})",
                                    "writer = path.open; other = writer; other(mode={mode})"])
def test_each_open_spelling_flags_an_unlisted_writer(mode, opener):
    src = 'def planted(transcript, path):\n    ' + opener.format(mode=repr(mode)) + '\n'
    site = "services/zoe-data/planted.py::planted"
    assert write_sites(src, "services/zoe-data/planted.py") == {site}
    assert ht.classify(site) is None


@pytest.mark.parametrize("statement", [
    "path.write_text(transcript)", "path.write_bytes(transcript)",
    "writer = path.write_text; writer(transcript)", "writer = path.write_bytes; writer(transcript)",
    'os.open(path, os.O_WRONLY | os.O_CREAT)', 'os.open(path, flags=os.O_RDWR)',
    'writer = os.open; writer(path, os.O_APPEND | os.O_WRONLY)',
    'from os import open as writer, O_WRONLY as flag; writer(path, flag)',
    'import os as sysos; flags = sysos.O_WRONLY | sysos.O_TRUNC; sysos.open(path, flags)',
    'from builtins import open as writer; writer(path, "w")',
    'import io as stream; writer = stream.open; writer(path, "wb")',
    'mode = "ab"; writer = path.open; writer(mode=mode)',
    'writer = path.open("w").write; writer(transcript)',
])
def test_each_other_write_spelling_flags_an_unlisted_writer(statement):
    site = "services/zoe-data/planted.py::planted"
    assert write_sites('def planted(transcript, path):\n    ' + statement + '\n',
                       "services/zoe-data/planted.py") == {site}
    assert ht.classify(site) is None


def test_read_modes_do_not_become_writers():
    assert write_sites('import os\ndef read_only(transcript, path):\n'
                       '    path.open("r"); path.open(mode="rb"); open(path, "r")\n'
                       '    os.open(path, os.O_RDONLY)\n', "x.py") == set()


def test_inventory_only_names_stores_and_imports_no_runtime_reader(monkeypatch):
    """The pendant allowlists permit metadata, never a new runtime reader or store open."""
    import builtins
    import sys
    import types

    sources = {name: (SVC / f"{name}.py").read_text() for name in ("household_text", "forget_reach")}
    allowed = {"__future__", "ast", "functools", "re", "fnmatch", "dataclasses", "pathlib", "typing", "sys", "forget_reach"}
    for source in sources.values():
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                assert {a.name for a in node.names} <= allowed
            elif isinstance(node, ast.ImportFrom):
                assert node.module in allowed
            elif isinstance(node, ast.Call):
                assert not (isinstance(node.func, ast.Name) and node.func.id in {"__import__", "eval", "exec"})
    for node in ast.parse(sources["household_text"]).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name != "main":
            assert not any(isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                           and n.func.attr in {"open", "read_text", "read_bytes", "write_text", "write_bytes"}
                           for n in ast.walk(node))
    def refuse(*args, **kwargs):
        raise AssertionError("metadata inventory attempted store I/O")
    # Execute BOTH local modules freshly; the only allowed file I/O is main's
    # explicit --write documentation command, which is not invoked on import.
    monkeypatch.setattr(builtins, "open", refuse)
    for method in ("open", "read_text", "read_bytes", "write_text", "write_bytes", "glob", "rglob", "iterdir"):
        monkeypatch.setattr(Path, method, refuse)
    for name, source in reversed(list(sources.items())):
        module = types.ModuleType(name)
        monkeypatch.setitem(sys.modules, name, module)
        exec(compile(source, f"{name}.py", "exec"), module.__dict__)
    assert module.counts()["stores"] > 0


def test_unerasable_copies_are_counted_as_gaps():
    keys = {"offbox_stage", "offbox_snapshots", "panel_voice_logs", "panel_browser_transcripts",
            "host_backup_archives", "restore_drill_staging", "chroma_rehearsal_copies", "ownerless_remediation_backups",
            "backup_legacy_db", "custom_memory_exports", "compaction_salvage", "night_job_logs", "precompact_archives",
            "backup_adhoc", "backup_postgres", "backup_palace_tar"}
    for key in keys:
        assert ht.reach(ht.BY_KEY[key]) == (fr.GAP, "-", "-")
        assert ht.BY_KEY[key].store_class == "copy"
        assert re.search(r"[\w/.-]+:\d+", ht.BY_KEY[key].evidence)
    assert ht.reach(ht.BY_KEY["voice_sample_corpus"]) == (fr.GAP, "-", "-")
    assert ht.counts()["neither"] >= len(keys) + 1


def test_the_detector_catches_removing_a_reviewed_entry(all_sites, monkeypatch):
    """Break-the-fix control on the real tree: drop one WRITE_SITES entry and the coverage check must go red."""
    site = "services/zoe-data/routers/telegram_media.py::_maybe_capture"
    monkeypatch.delitem(ht.WRITE_SITES, site)
    assert ht.classify(site) is None and site in all_sites


def test_nested_functions_are_judged_on_their_own_scope():
    src = ('def outer(transcript):\n'
           '    def inner(cfg, p):\n'
           '        p.write_text(cfg)\n'
           '    return inner\n')
    assert write_sites(src, "x.py") == set()


# ── 2. tables with a text column ──────────────────────────────────────────────────────────────

_TEXT_COL = re.compile(r'(?:^|[(,])\s*"?([a-z_][a-z0-9_]*)"?\s+(?:TEXT|VARCHAR|CHARACTER VARYING|JSONB?)\b', re.I | re.M)
_TEXT_COL_NAMES = re.compile(r"(?:^|_)(text|content|body|title|description|summary|note|notes|message|transcript|utterance|reply"
                             r"|prompt|query|label|payload|words|said|line)(?:$|_)", re.I)

#: Tables with a text-like column that hold no household words (and no member column, so forget_reach does not list them)
NO_TEXT_TABLES: dict[str, str] = {
    "panels": "a panel's name, room and settings",
    "panel_client_certs": "a client certificate fingerprint and an owner note about the device",
    "panel_presence_events": "presence events: a type, a payload of sensor values and a confidence",
    "people_field_definitions": "the LABELS of custom person fields (the values, which hold words, are people_field_values)",
    "struggle_proposals": "the text-free failure pattern by design (struggle_proposals.py module doc)",
    "trust_allowlist": "tool-name patterns the owner allowed",
}


def text_tables(sources) -> set[str]:
    out = set()
    for src in sources:
        text = Path(src).read_text(encoding="utf-8", errors="replace")
        for name, body in fr._bodies(text):
            if any(_TEXT_COL_NAMES.search(c) for c in _TEXT_COL.findall(body)):
                out.add(name)
    return out


def _schema_sources() -> list[Path]:
    return [p for p in _py_files() if p.is_relative_to(SVC)
            and ("alembic" in p.parts or "CREATE TABLE" in p.read_text(encoding="utf-8", errors="replace"))]


def test_every_table_with_a_text_column_is_in_an_inventory():
    known = {s.key for s in fr.TABLES} | set(NO_TEXT_TABLES) | set(re.findall(r"pg:([a-z_0-9]+)", " ".join(s.location for s in ht.EXTRA)))
    missing = sorted(text_tables(_schema_sources()) - known)
    assert not missing, (f"tables with a text column in no inventory: {missing}. A table that can hold household words goes in "
                         "forget_reach.TABLES (and here); one that cannot goes in NO_TEXT_TABLES with the reason")


def test_the_table_detector_catches_a_new_text_table(tmp_path):
    mig = tmp_path / "9999_fake.py"
    mig.write_text('X = """CREATE TABLE fake_diary (id TEXT PRIMARY KEY, body TEXT, ts BIGINT)"""\n'
                   'Y = """CREATE TABLE fake_counter (id TEXT PRIMARY KEY, n INTEGER)"""\n')
    assert text_tables([mig]) == {"fake_diary"}


# ── 3. the registry itself ────────────────────────────────────────────────────────────────────

def test_every_store_of_words_in_forget_reach_is_here():
    missing = sorted(ht.must_cover() - {s.key for s in ht.FROM_FORGET_REACH})
    assert not missing, f"forget_reach stores of words with no household-text entry: {missing}"
    assert {s.key for s in ht.FROM_FORGET_REACH} <= set(fr.BY_KEY)
    assert not {s.key for s in ht.EXTRA} & set(fr.BY_KEY), "an EXTRA store is in forget_reach: take it from there"


def test_every_entry_is_complete_and_names_no_real_value():
    bad = []
    for s in ht.STORES:
        for f in ("location", "text", "retention", "owner"):
            if len(getattr(s, f).strip()) < 3:
                bad.append(f"{s.key}: {f} missing")
        if re.search(r"@|/home/|\b\d{1,3}(\.\d{1,3}){3}\b", s.location):
            bad.append(f"{s.key}: the location must be a pattern, not a real path or address")
        st, _fo, _de = ht.reach(s)
        if st not in (fr.REACHED, fr.GAP, fr.OUT_OF_SCOPE):
            bad.append(f"{s.key}: status {st!r}")
        if s in ht.EXTRA and st != fr.REACHED and len(s.why) < 15:
            bad.append(f"{s.key}: a gap or out-of-scope store needs a reason")
    assert not bad, bad
    assert len(ht.BY_KEY) == len(ht.STORES)


def _ledger() -> set[str]:
    text = (REPO / "docs" / "knowledge" / "open-problems.md").read_text(encoding="utf-8")
    line = next((l for l in text.splitlines() if "household-text gaps:" in l), "")
    return set(re.findall(r"`([a-z_0-9]+)`", line.split("household-text gaps:", 1)[1].split(" · ", 1)[0])) if line else set()


def test_extra_gaps_are_ledgered_and_the_ledger_is_not_stale():
    gaps = {s.key for s in ht.EXTRA if s.status == fr.GAP}
    assert gaps == _ledger(), f"open-problems.md 'household-text gaps:' must list exactly {sorted(gaps)}"


def test_the_docs_page_carries_the_generated_inventory():
    doc = (REPO / "docs" / "knowledge" / "household-text-inventory.md").read_text(encoding="utf-8")
    assert ht.render_markdown() in doc, ("docs/knowledge/household-text-inventory.md is stale: "
                                         "python3 services/zoe-data/household_text.py --write docs/knowledge/household-text-inventory.md")
