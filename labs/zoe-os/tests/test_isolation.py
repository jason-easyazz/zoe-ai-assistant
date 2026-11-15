"""The lab stays a lab: nothing outside labs/zoe-os may import the kernel (stdlib, no sockets, no live reads)."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
IMPORT = re.compile(r"^\s*(?:from|import)\s+zoe_os\b", re.M)


def importers(root: Path, skip: Path):
    for p in root.rglob("*.py"):
        if skip in p.parents or "node_modules" in p.parts:
            continue
        if IMPORT.search(p.read_text(encoding="utf-8", errors="ignore")):
            yield p


def test_production_services_never_import_the_lab_kernel():
    assert list(importers(REPO / "services", REPO / "labs")) == []


def test_the_scanner_does_see_an_import(tmp_path):
    (tmp_path / "bad.py").write_text("from zoe_os.spine import X\n")
    (tmp_path / "ok.py").write_text("import os\n")
    assert [p.name for p in importers(tmp_path, tmp_path / "none")] == ["bad.py"]
