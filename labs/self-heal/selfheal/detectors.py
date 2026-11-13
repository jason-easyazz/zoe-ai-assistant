"""Fault detectors. Each one only READS and yields heal tickets.

v1 ships ONE detector: a failing test on the base branch in the offline pytest lane. Its
reproduction is the pytest node id, which makes "is the fault gone?" a yes/no question.
(Service-traceback and voice-probe detectors are not in v1; see README.)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

FAILING_TEST = "failing_test"
COLLECTION_ERROR = "collection_error"


@dataclass
class Ticket:
    id: str
    fault_class: str
    fault_key: str                 # stable across runs: same fault -> same key (for caps / parking)
    summary: str
    evidence: dict                 # ids, hashes and node ids ONLY: never failure text
    repro_cmd: list                # argv, run from the repo root; exit 0 = healed
    repro_files: list = field(default_factory=list)   # restored from base_sha before verify
    base_sha: str = ""
    created: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Ticket":
        return cls(**d)


def fault_key(fault_class: str, ident: str) -> str:
    return hashlib.sha1(f"{fault_class}:{ident}".encode()).hexdigest()[:12]


def new_ticket_id(now: dt.datetime, key: str) -> str:
    return f"{now:%Y%m%d-%H%M%S}-{key[:6]}"


def _nodeid(case: ET.Element) -> tuple[str, str]:
    """junit `classname`=`pkg.dir.test_mod.TestCls`, `file` attr when present -> pytest node id."""
    name = case.get("name", "")
    file_ = case.get("file")
    cls = case.get("classname", "")
    parts = cls.split(".")
    if not file_:
        # walk back to the module part (test_*.py); the rest is the class chain
        mod_idx = max((i for i, p in enumerate(parts) if p.startswith("test_") or p.endswith("_test")), default=len(parts) - 1)
        file_ = "/".join(parts[: mod_idx + 1]) + ".py"
        chain = parts[mod_idx + 1:]
    else:
        stem = Path(file_).stem
        chain = parts[parts.index(stem) + 1:] if stem in parts else []
    return file_, "::".join([file_, *chain, name])


def _is_collection_error(case: ET.Element) -> bool:
    """pytest writes a module that fails to import as a testcase with an EMPTY classname, the
    dotted module as its name and `<error message="collection failure">`."""
    err = case.find("error")
    return err is not None and (case.get("classname", "") == "" or
                                (err.get("message") or "").startswith("collection failure"))


def collection_errors(xml_text: str) -> list[str]:
    """File names (only) of the modules that failed to COLLECT."""
    out = []
    for case in ET.fromstring(xml_text).iter("testcase"):
        if _is_collection_error(case):
            name = case.get("file") or case.get("name", "").replace(".", "/") + ".py"
            out.append(name)
    return sorted(set(out))


def node_passed(xml_text: str, nodeid: str) -> str:
    """'passed' only when that node RAN and passed: not skipped, xfailed, deselected, absent
    ('no tests ran') or erroring; otherwise the reason class."""
    try:
        cases = [c for c in ET.fromstring(xml_text).iter("testcase") if _nodeid(c)[1] == nodeid]
    except ET.ParseError:
        return "junit_unreadable"
    if len(cases) != 1:
        return "not_run" if not cases else "ambiguous"
    for tag in ("failure", "error", "skipped"):
        if cases[0].find(tag) is not None:
            return tag   # pytest records xfail as <skipped type="pytest.xfail">
    return "passed"


def node_statuses(xml_text: str) -> dict:
    """{nodeid: passed | failure | error | skipped} for every test that RAN (xfail = skipped)."""
    out = {}
    for c in ET.fromstring(xml_text).iter("testcase"):
        if not _is_collection_error(c):
            out[_nodeid(c)[1]] = next((t for t in ("failure", "error", "skipped") if c.find(t) is not None), "passed")
    return out


def parse_junit(xml_text: str) -> list[dict]:
    """Failing / erroring test cases from a pytest junit report: [{nodeid, file, message}].
    Collection errors are NOT test failures: see collection_errors()."""
    out = []
    root = ET.fromstring(xml_text)
    for case in root.iter("testcase"):
        if _is_collection_error(case):
            continue
        bad = case.find("failure")
        if bad is None:
            bad = case.find("error")
        if bad is None:
            continue
        file_, nodeid = _nodeid(case)
        # The failure TEXT may carry anything (assertion values, user data): only its hash leaves here.
        text = (bad.get("message") or "") + (bad.text or "")
        out.append({"nodeid": nodeid, "file": file_, "kind": bad.tag,
                    "failure_hash": hashlib.sha1(text.encode()).hexdigest()[:12]})
    return out


def failing_test_tickets(junit_xml: str, *, base_sha: str, python: str, ignore: list,
                         now: dt.datetime) -> list[Ticket]:
    tickets = []
    for f in parse_junit(junit_xml):
        if f["nodeid"] in ignore:
            continue
        key = fault_key(FAILING_TEST, f["nodeid"])
        tickets.append(Ticket(
            id=new_ticket_id(now, key), fault_class=FAILING_TEST, fault_key=key,
            summary=f"failing test on base: {f['nodeid']}",
            evidence={"nodeid": f["nodeid"], "kind": f["kind"], "failure_hash": f["failure_hash"],
                      "base_sha": base_sha},
            repro_cmd=[python, "-m", "pytest", "-p", "no:cacheprovider", "-q", f["nodeid"]],
            repro_files=[f["file"]], base_sha=base_sha, created=now.isoformat(timespec="seconds"),
        ))
    return tickets


def run_detector(cfg, checkout: str, lock: list | None = None) -> str | None:
    """Run the configured offline lane (sandboxed) in the base checkout; the junit XML, or None when
    the lane wrote none (timeout, crash): that is `detect_failed`, never "no fault"."""
    from .sandbox import Sandbox   # noqa: PLC0415
    junit = Path(checkout) / ".self-heal-junit.xml"
    rc, _ = Sandbox(cfg).run([*cfg.detect_cmd, f"--junitxml={junit}"], checkout, cfg.detect_timeout_s, lock or [])
    try:
        return junit.read_text() if rc is not None and junit.exists() else None
    finally:
        junit.unlink(missing_ok=True)
