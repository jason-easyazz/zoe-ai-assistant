"""The lab stays a lab: nothing outside labs/zoe-os may import the kernel (stdlib, no sockets, no live reads)."""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
IMPORT = re.compile(r"^\s*(?:from|import)\s+zoe_os\b", re.M)


def importers(root: Path, skip: Path, *, tests_may: bool = False):
    for p in root.rglob("*.py"):
        if skip in p.parents or "node_modules" in p.parts:
            continue
        if tests_may and "tests" in p.relative_to(root).parts:
            continue                     # a contract TEST may drive the lab over its wire; production code may not import it
        if IMPORT.search(p.read_text(encoding="utf-8", errors="ignore")):
            yield p


def test_production_services_never_import_the_lab_kernel():
    assert list(importers(REPO / "services", REPO / "labs", tests_may=True)) == []


def test_only_a_tests_directory_is_exempt(tmp_path):
    (tmp_path / "svc").mkdir()
    (tmp_path / "svc" / "tests").mkdir()
    (tmp_path / "svc" / "app.py").write_text("import zoe_os\n")
    (tmp_path / "svc" / "tests" / "test_x.py").write_text("from zoe_os import wire\n")
    assert [p.name for p in importers(tmp_path, tmp_path / "none", tests_may=True)] == ["app.py"]


def test_the_scanner_does_see_an_import(tmp_path):
    (tmp_path / "bad.py").write_text("from zoe_os.spine import X\n")
    (tmp_path / "ok.py").write_text("import os\n")
    assert [p.name for p in importers(tmp_path, tmp_path / "none")] == ["bad.py"]
