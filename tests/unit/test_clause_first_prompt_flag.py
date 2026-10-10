"""ZOE_VOICE_CLAUSE_FIRST_PROMPT wiring (labs/flue-zoe-brain-2x/src/agents/zoe.ts): default OFF, one static paragraph, read once.

The behavioural pins run in node (labs/flue-zoe-brain-2x/test/clause_first_prompt.test.ts); this lane is the CI-safe structural guard
that the flag keeps its shape: parsed by one helper with an explicit on-list, never read per turn inside the agent render, and the
instruction is a constant with no interpolation (a varying system prompt breaks the llama prefix cache).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.ci_safe

REPO = Path(__file__).resolve().parents[2]
ZOE_TS = (REPO / "labs" / "flue-zoe-brain-2x" / "src" / "agents" / "zoe.ts").read_text()
FLAG = "ZOE_VOICE_CLAUSE_FIRST_PROMPT"


def test_the_flag_is_read_in_exactly_one_place_and_only_an_explicit_on_value_enables_it():
    assert ZOE_TS.count(f"env.{FLAG}") == 1
    m = re.search(r"\[((?:'[a-z0-9]+',? ?)+)\]\.includes\(", ZOE_TS)
    assert m and set(re.findall(r"'([a-z0-9]+)'", m.group(1))) == {"1", "true", "yes", "on"}


def test_the_instructions_are_computed_once_at_module_load_never_inside_the_render():
    assert "export const ZOE_INSTRUCTIONS = buildZoeInstructions(clauseFirstPromptEnabled());" in ZOE_TS
    body = ZOE_TS.split("export function Zoe(): string {", 1)[1].split("Zoe.agentName", 1)[0]
    assert "clauseFirstPromptEnabled" not in body and FLAG not in body


def test_the_doctrine_is_a_static_constant_so_the_prompt_prefix_cache_keeps_hitting():
    block = ZOE_TS.split("export const CLAUSE_FIRST_DOCTRINE = [", 1)[1].split("].join(", 1)[0]
    assert "${" not in block and "Date" not in block
    assert "never guess, skip a tool" in block                     # style pressure must not buy a fast wrong answer
    assert len(block) < 450


def test_the_off_path_is_the_old_string():
    assert "const voice = clauseFirst ? `${VOICE_DELIVERY_DOCTRINE}\\n\\n${CLAUSE_FIRST_DOCTRINE}` : VOICE_DELIVERY_DOCTRINE;" in ZOE_TS
