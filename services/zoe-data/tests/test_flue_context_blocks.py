"""Flue history hygiene: the zoe-data ↔ sidecar marker table + the budget log.

The sidecar (labs/flue-zoe-brain-2x/src/context-blocks.ts) elides injected
blocks from older user messages under ZOE_BRAIN_ELIDE_STALE_BLOCKS by matching
``FLUE_CONTEXT_BLOCKS``. A drift is SILENT (nothing matches, every stale copy is
replayed), so the two tables are pinned equal here, and every block this seam
actually emits must be matched by them under the sidecar's open-line rule.
"""
import logging
import re
from pathlib import Path

import pytest

import zoe_flue_client as zfc

pytestmark = pytest.mark.ci_safe

_TS = Path(__file__).resolve().parents[3] / "labs/flue-zoe-brain-2x/src/context-blocks.ts"


def _ts_table() -> list[tuple[str, str]]:
    body = re.search(r"FLUE_CONTEXT_BLOCKS[^=]*= \[(.*?)\n\];", _TS.read_text(), re.DOTALL)
    assert body, "FLUE_CONTEXT_BLOCKS moved — update this pin"
    return re.findall(r"\['([^']+)', '([^']+)'\]", body.group(1))


def _is_open(line: str) -> bool:  # the sidecar's openType rule
    return line.endswith("]") and any(
        line == f"{p}]" or line.startswith(f"{p} ") for p, _ in zfc._FLUE_CONTEXT_BLOCKS)


def test_python_and_ts_tables_are_equal():
    assert _ts_table() == list(zfc._FLUE_CONTEXT_BLOCKS)


@pytest.mark.parametrize("open_line,close_line", [
    (zfc._RECALL_BLOCK_OPEN, zfc._RECALL_BLOCK_CLOSE),
    (zfc._CONTINUITY_BLOCK_OPEN, zfc._CONTINUITY_BLOCK_CLOSE),
    (zfc._OFFER_BLOCK_OPEN, zfc._OFFER_BLOCK_CLOSE),
    ("[Today 2026-09-29]", "[END Today]"),  # #1781's dated Flue label
])
def test_every_emitted_block_is_matched(open_line, close_line):
    closes = {c for _, c in zfc._FLUE_CONTEXT_BLOCKS}
    assert _is_open(open_line) and close_line in closes
    assert not _is_open("the [MEMORY CONTEXT] marker")  # inline mention is content


def test_context_budget_logged_and_absent_is_silent(caplog):
    caplog.set_level(logging.INFO, logger=zfc.logger.name)
    budget = {"system": 2400, "tools": 700, "history": 900, "tail": 60, "stale": 310, "elided": 1}
    zfc._log_prompt_cache("s1", {"done": True, "context_budget": budget})
    assert [r.getMessage() for r in caplog.records] == [
        "FLUE_CONTEXT_BUDGET session=s1 system=2400 tools=700 history=900 tail=60 stale=310 elided=1"]
    caplog.clear()
    zfc._log_prompt_cache("s1", {"done": True, "context_budget": "junk"})
    zfc._log_prompt_cache("s1", {"done": True})
    assert caplog.records == []
