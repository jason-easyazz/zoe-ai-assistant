"""The portrait synthesis prompt must fit the brain's context (B6.6, PR #1716).

run_portrait_synthesis() calls llama-server DIRECTLY (gemma_base()), not through
the Flue client that windows chat turns to 8192 tokens, and it used to pass up to
120 facts + 30 insights + 10 journal entries with no budget. With --ctx-size 8192
an oversize prompt is refused by llama-server and the portrait never regenerates.
"""

import logging

import pytest

import user_portrait
from user_portrait import PORTRAIT_SYNTHESIS_PROMPT, build_portrait_prompt

pytestmark = pytest.mark.ci_safe

INSTRUCTIONS = PORTRAIT_SYNTHESIS_PROMPT.split("{memory_facts}")[0]


def _pre_budget_prompt(fact_lines, insight_lines, journal_entries):
    """The exact expression run_portrait_synthesis() used before the budget."""
    memory_facts = "\n".join(fact_lines[:120]) if fact_lines else "(none yet)"
    insights = "\n".join(insight_lines[:30]) if insight_lines else "(none yet)"
    journal_text = "\n\n".join(journal_entries) if journal_entries else "(none)"
    return PORTRAIT_SYNTHESIS_PROMPT.format(
        memory_facts=memory_facts, insights=insights, journal_entries=journal_text
    )


def _est(text):
    return len(text) // 4


def _big_fixture():
    facts = [f"- FACT{i:03d} " + ("likes long walks by the river and talks about it " * 8) for i in range(200)]
    insights = [f"- INSIGHT{i:02d} " + ("tends to plan ahead when stressed " * 6) for i in range(30)]
    journal = [f"[2026-09-{i + 1:02d}] Entry {i}: " + ("a" * 300) for i in range(10)]
    return facts, insights, journal


@pytest.mark.parametrize(
    "facts,insights,journal",
    [
        (["- likes tea", "- has a dog called Max"], ["- values quiet mornings"], ["[2026-09-02] Day: fine", "[2026-09-01] Mood: calm"]),
        ([], [], []),
        ([f"- fact {i}" for i in range(150)], [f"- insight {i}" for i in range(40)], []),
    ],
)
def test_under_budget_prompt_is_byte_identical_to_pre_budget(facts, insights, journal, caplog):
    caplog.set_level(logging.INFO, logger="user_portrait")
    assert build_portrait_prompt(facts, insights, journal) == _pre_budget_prompt(facts, insights, journal)
    assert not [r for r in caplog.records if "trimmed" in r.getMessage()]


def test_oversize_prompt_fits_budget_and_keeps_instructions(caplog):
    facts, insights, journal = _big_fixture()
    assert _est(_pre_budget_prompt(facts, insights, journal)) > 8192  # the fixture really is oversize
    caplog.set_level(logging.INFO, logger="user_portrait")

    prompt = build_portrait_prompt(facts, insights, journal, user_id="demo-user")

    assert _est(prompt) <= user_portrait.PORTRAIT_PROMPT_BUDGET_TOKENS
    assert prompt.startswith(INSTRUCTIONS)
    for header in ("[MEMORY FACTS", "[SYNTHESIZED INSIGHTS", "[RECENT JOURNAL ENTRIES"):
        assert header in prompt
    # Best-ranked items survive, the tail is what gets dropped.
    assert "FACT000" in prompt and "FACT199" not in prompt
    assert "INSIGHT00" in prompt

    msgs = [r.getMessage() for r in caplog.records if "trimmed" in r.getMessage()]
    assert len(msgs) == 1
    assert "user=demo-user" in msgs[0] and "facts=200->" not in msgs[0]  # capped at 120 before trimming
    assert "facts=120->" in msgs[0]
    assert "river" not in msgs[0] and "FACT" not in msgs[0]  # counts only, never fact text


def test_single_giant_item_is_clipped_under_budget():
    prompt = build_portrait_prompt(["- " + "x" * 60000], [], [])
    assert _est(prompt) <= user_portrait.PORTRAIT_PROMPT_BUDGET_TOKENS
    assert prompt.startswith(INSTRUCTIONS)
    assert "…" in prompt


@pytest.mark.asyncio
async def test_run_portrait_synthesis_sends_budgeted_prompt(monkeypatch):
    import memory_service

    facts, _, journal = _big_fixture()

    class _Ref:
        def __init__(self, text):
            self.text = text
            self.metadata = {}

    class _Svc:
        async def load_for_prompt(self, user_id, limit=20):
            return [_Ref(f[2:]) for f in facts[:limit]]

    class _Cur:
        async def fetchall(self):
            return [(f"T{i}", "b" * 400, None, f"2026-09-{i + 1:02d}") for i in range(10)]

    class _Db:
        async def execute(self, sql, params=()):
            return _Cur()

        async def commit(self):
            pass

    monkeypatch.setattr(memory_service, "get_memory_service", lambda: _Svc())
    sent = {}

    async def _fake_llm(prompt):
        sent["prompt"] = prompt
        return "portrait text"

    monkeypatch.setattr(user_portrait, "_call_llm_for_portrait", _fake_llm)

    result = await user_portrait.run_portrait_synthesis("demo-user", db=_Db())

    assert result["status"] == "ok"
    assert _est(sent["prompt"]) <= user_portrait.PORTRAIT_PROMPT_BUDGET_TOKENS
    assert sent["prompt"].startswith(INSTRUCTIONS)
