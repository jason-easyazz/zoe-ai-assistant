"""Fault detectors. Each one only READS and yields heal tickets.

v1 ships ONE detector: a failing test on the base branch in the offline pytest lane. Its
reproduction is the pytest node id, which makes "is the fault gone?" a yes/no question.
(Service-traceback and voice-probe detectors are not in v1; see README.)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from pathlib import Path

FAILING_TEST = "failing_test"


@dataclass
class Ticket:
    id: str
    fault_class: str
    fault_key: str                 # stable across runs: same fault -> same key (for caps / parking)
    summary: str
    evidence: dict
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


def parse_junit(xml_text: str) -> list[dict]:
    """Failing / erroring test cases from a pytest junit report: [{nodeid, file, message}]."""
    out = []
    root = ET.fromstring(xml_text)
    for case in root.iter("testcase"):
        bad = case.find("failure")
        if bad is None:
            bad = case.find("error")
        if bad is None:
            continue
        file_, nodeid = _nodeid(case)
        msg = (bad.get("message") or (bad.text or "")).strip().splitlines()
        out.append({"nodeid": nodeid, "file": file_, "message": (msg[0] if msg else "")[:300]})
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
            evidence={"nodeid": f["nodeid"], "first_line": f["message"], "base_sha": base_sha},
            repro_cmd=[python, "-m", "pytest", "-p", "no:cacheprovider", "-q", f["nodeid"]],
            repro_files=[f["file"]], base_sha=base_sha, created=now.isoformat(timespec="seconds"),
        ))
    return tickets


def run_failing_test_detector(checkout: str, detect_cmd: list, *, timeout: int, base_sha: str,
                              python: str, ignore: list, now: dt.datetime,
                              lock: list | None = None) -> list[Ticket]:
    """Run the configured offline lane in a read-only base checkout and ticket its failures."""
    junit = Path(checkout) / ".self-heal-junit.xml"
    cmd = [*(lock or []), *detect_cmd, f"--junitxml={junit}"]
    subprocess.run(cmd, cwd=checkout, capture_output=True, text=True, timeout=timeout)
    if not junit.exists():
        return []
    try:
        return failing_test_tickets(junit.read_text(), base_sha=base_sha, python=python,
                                    ignore=ignore, now=now)
    finally:
        junit.unlink(missing_ok=True)
