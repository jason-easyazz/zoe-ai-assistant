"""The weekly memory-synthesis prompt must fit the brain's single 8192-token slot
(B6.6, PR #1716). It concatenates up to ten stored documents with no length limit
and posts straight to llama-server; an oversize request is refused and the insight
is silently skipped."""

import logging

import pytest

import memory_digest

pytestmark = pytest.mark.ci_safe


def test_ten_long_docs_fit_the_chars_over_2_bound(caplog):
    sample = [(f"id{i}", f"DOC{i} " + "remembers the long drive to the coast " * 110) for i in range(10)]
    assert all(len(d) > 4000 for _, d in sample)
    caplog.set_level(logging.INFO, logger="memory_digest")

    prompt = memory_digest._build_synthesis_prompt("travel", sample)

    assert len(prompt) // 2 <= memory_digest._SYNTHESIS_PROMPT_BUDGET_TOKENS
    assert memory_digest._SYNTHESIS_PROMPT_BUDGET_TOKENS == memory_digest._BRAIN_SLOT_TOKENS * 2 // 3
    assert prompt.startswith("The following 10 memory facts all share the topic \"travel\".")
    assert all(f"- DOC{i} " in prompt for i in range(10))  # every doc keeps a share
    msgs = [r.getMessage() for r in caplog.records if "truncated" in r.getMessage()]
    assert msgs and "remembers" not in msgs[0] and "travel" not in msgs[0]  # counts only


def test_short_docs_are_byte_identical_to_the_old_prompt():
    sample = [(f"id{i}", f"likes tea {i}") for i in range(6)]
    old = memory_digest._SYNTHESIS_PROMPT.format(
        n=6, tag="tea", facts="\n".join(f"- {doc}" for _, doc in sample))
    assert memory_digest._build_synthesis_prompt("tea", sample) == old


def test_synthesis_pass_uses_the_budgeted_builder():
    import inspect

    src = inspect.getsource(memory_digest._synthesis_pass)
    assert "_build_synthesis_prompt(tag, sample)" in src
    assert "_SYNTHESIS_PROMPT.format" not in src
