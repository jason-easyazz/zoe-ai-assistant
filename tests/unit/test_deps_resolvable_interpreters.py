"""deps-resolvable must resolve each manifest on the Python it is installed with.

Resolvability is Python-version-specific: resolving the containers' 3.11 manifests
on the host's 3.10 reddened the job on valid pins (starlette 1.10.0 in the HA bridge),
and resolving a 3.10 manifest on 3.11 would bless pins the Jetson cannot install.
"""
import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
# manifest -> the Dockerfile whose FROM line names its interpreter (None = the Jetson host, 3.10)
RUNTIME = {
    "services/zoe-data/requirements.txt": None,
    "services/zoe-auth/requirements.txt": "services/zoe-auth/Dockerfile",
    "services/homeassistant-mcp-bridge/requirements.txt": "services/homeassistant-mcp-bridge/Dockerfile",
    "labs/setfit-router/requirements.txt": None,
}
HOST_PY = "3.10"


def _job():
    wf = yaml.safe_load((REPO / ".github/workflows/validate.yml").read_text(encoding="utf-8"))
    return wf["jobs"]["deps-resolvable"]


def _manifest_table() -> dict:
    run = next(s["run"] for s in _job()["steps"] if s.get("name") == "Resolve every requirements manifest")
    block = re.search(r'MANIFESTS="\n(.*?)\n\s*"', run, re.S).group(1)
    return dict(line.split() for line in block.strip().splitlines())


def _image_python(dockerfile: str) -> str:
    text = (REPO / dockerfile).read_text(encoding="utf-8")
    m = re.search(r"^FROM\s+python:(\d+\.\d+)", text, re.M)
    assert m, f"{dockerfile}: no python:<ver> FROM line"
    return m.group(1)


def test_every_manifest_resolves_on_its_runtime_python():
    table = _manifest_table()
    assert set(table) == set(RUNTIME), "manifest list changed: map its runtime here"
    for manifest, dockerfile in RUNTIME.items():
        want = _image_python(dockerfile) if dockerfile else HOST_PY
        assert table[manifest] == want, f"{manifest} resolves on {table[manifest]} but runs on {want}"


def test_every_interpreter_the_table_uses_is_set_up():
    setup = next(s for s in _job()["steps"] if str(s.get("uses", "")).startswith("actions/setup-python"))
    versions = {v.strip() for v in str(setup["with"]["python-version"]).split() if v.strip()}
    assert set(_manifest_table().values()) <= versions
    # the default python3 (last listed) runs the ceiling assertions and has `packaging` installed
    assert str(setup["with"]["python-version"]).split()[-1] == HOST_PY
