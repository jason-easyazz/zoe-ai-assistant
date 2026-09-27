"""The portrait synthesis prompt must fit the brain's context (B6.6, PR #1716).

Counted with llama-server's own tokenizer (POST /tokenize). When that endpoint is
down the budget fails CLOSED on a conservative chars/2 bound; prompts already under
budget are byte-identical to the pre-budget prompt either way.

run_portrait_synthesis() calls llama-server DIRECTLY (gemma_base()), not through
the Flue client that windows chat turns to 8192 tokens, and it used to pass up to
120 facts + 30 insights + 10 journal entries with no budget. With --ctx-size 8192
an oversize prompt is refused by llama-server and the portrait never regenerates.
"""

import logging

import pytest

import user_portrait
from user_portrait import PORTRAIT_SYNTHESIS_PROMPT, _fit_by_estimate, build_portrait_prompt

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


async def _no_tokenizer(text):
    return None


def _scaled_tokenizer(factor, calls=None):
    async def count(text):
        if calls is not None:
            calls.append(len(text))
        return (len(text) // 4) * factor
    return count


def _fallback_reference(facts, insights, journal):
    """What the tokenizer-less path must send: trimmed until chars/2 <= budget."""
    f, i, j = list(facts[:120]), list(insights[:30]), list(journal)
    prompt = user_portrait._render_portrait_prompt(f, i, j)
    b = user_portrait.PORTRAIT_PROMPT_BUDGET_TOKENS
    if _est(prompt) * 2 <= b:
        return prompt
    return _fit_by_estimate(f, i, j, b // 2)[0]


def _chars4_only(facts, insights, journal):
    """The first (chars/4-only) version of the budget — what a 3x-dense text overflows."""
    f, i, j = list(facts[:120]), list(insights[:30]), list(journal)
    return _fit_by_estimate(f, i, j, user_portrait.PORTRAIT_PROMPT_BUDGET_TOKENS)[0]


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
@pytest.mark.asyncio
@pytest.mark.parametrize("counter", [_no_tokenizer, _scaled_tokenizer(1)], ids=["fallback", "tokenizer"])
async def test_under_budget_prompt_is_byte_identical_to_pre_budget(facts, insights, journal, counter, caplog):
    caplog.set_level(logging.INFO, logger="user_portrait")
    got = await build_portrait_prompt(facts, insights, journal, count_tokens=counter)
    assert got == _pre_budget_prompt(facts, insights, journal)
    assert not [r for r in caplog.records if "trimmed" in r.getMessage()]


@pytest.mark.asyncio
async def test_oversize_prompt_fits_budget_and_keeps_instructions(caplog):
    facts, insights, journal = _big_fixture()
    assert _est(_pre_budget_prompt(facts, insights, journal)) > 8192  # the fixture really is oversize
    caplog.set_level(logging.INFO, logger="user_portrait")

    prompt = await build_portrait_prompt(facts, insights, journal, user_id="demo-user", count_tokens=_no_tokenizer)

    assert _est(prompt) * 2 <= user_portrait.PORTRAIT_PROMPT_BUDGET_TOKENS  # fail-closed chars/2 bound
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


@pytest.mark.asyncio
async def test_single_giant_item_is_clipped_under_budget():
    prompt = await build_portrait_prompt(["- " + "x" * 60000], [], [], count_tokens=_no_tokenizer)
    assert _est(prompt) * 2 <= user_portrait.PORTRAIT_PROMPT_BUDGET_TOKENS
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
    counted = []
    monkeypatch.setattr(user_portrait, "_tokenize_count", _scaled_tokenizer(1, counted))

    result = await user_portrait.run_portrait_synthesis("demo-user", db=_Db())

    assert result["status"] == "ok"
    assert _est(sent["prompt"]) <= user_portrait.PORTRAIT_PROMPT_BUDGET_TOKENS
    assert sent["prompt"].startswith(INSTRUCTIONS)
    assert counted, "run_portrait_synthesis must count the prompt with the tokenizer"


@pytest.mark.asyncio
async def test_token_dense_text_trims_to_the_real_count(caplog):
    """A tokenizer that sees 3x the chars/4 estimate (CJK/emoji-dense text) forces
    more trimming than the estimate alone, and the REAL count ends under budget."""
    facts, insights, journal = _big_fixture()
    calls = []
    counter = _scaled_tokenizer(3, calls)
    caplog.set_level(logging.INFO, logger="user_portrait")

    prompt = await build_portrait_prompt(facts, insights, journal, user_id="demo-user", count_tokens=counter)

    budget = user_portrait.PORTRAIT_PROMPT_BUDGET_TOKENS
    assert _est(prompt) * 3 <= budget  # the real (fake-tokenizer) count fits
    estimate_only = _chars4_only(facts, insights, journal)
    assert _est(estimate_only) * 3 > budget  # what chars/4 alone would have sent overflows
    assert len(prompt) < len(estimate_only)
    assert prompt.startswith(INSTRUCTIONS) and "FACT000" in prompt
    assert 2 <= len(calls) <= 5  # one count, then bounded re-counts
    msg = [r.getMessage() for r in caplog.records if "trimmed" in r.getMessage()][0]
    assert "counter=tokenizer" in msg


@pytest.mark.asyncio
async def test_tokenizer_failure_fails_closed_on_chars_over_2():
    facts, insights, journal = _big_fixture()
    got = await build_portrait_prompt(facts, insights, journal, count_tokens=_no_tokenizer)
    budget = user_portrait.PORTRAIT_PROMPT_BUDGET_TOKENS
    assert len(got) // 2 <= budget  # chars/2 bound holds
    assert _est(_chars4_only(facts, insights, journal)) * 2 > budget  # chars/4 alone would not
    assert got == _fallback_reference(facts, insights, journal)
    assert got.startswith(INSTRUCTIONS) and "FACT000" in got
    # small prompts are unchanged on the fallback path
    small = (["- likes tea"], ["- values quiet mornings"], ["[2026-09-01] Day: fine"])
    assert await build_portrait_prompt(*small, count_tokens=_no_tokenizer) == _pre_budget_prompt(*small)


@pytest.mark.asyncio
async def test_real_tokenize_endpoint_down_warns_once_and_falls_back(monkeypatch, caplog):
    import gemma_endpoint  # noqa: F401 — gemma_base() reads GEMMA_SERVER_URL at call time

    monkeypatch.setenv("GEMMA_SERVER_URL", "http://127.0.0.1:9")  # discard port: refused
    monkeypatch.setattr(user_portrait, "_tokenize_warned", False)
    monkeypatch.setattr(user_portrait, "_TOKENIZE_TIMEOUT_S", 1.0)
    caplog.set_level(logging.WARNING, logger="user_portrait")
    facts, insights, journal = _big_fixture()

    first = await build_portrait_prompt(facts, insights, journal)
    second = await build_portrait_prompt(["- small"], [], [])

    assert first == _fallback_reference(facts, insights, journal)
    assert second == _pre_budget_prompt(["- small"], [], [])
    warns = [r for r in caplog.records if r.levelno == logging.WARNING and "/tokenize" in r.getMessage()]
    assert len(warns) == 1
