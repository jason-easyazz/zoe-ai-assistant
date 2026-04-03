from chat_session_title import derive_session_title, title_is_weak


def test_derive_truncates_words():
    t = derive_session_title("How do I plan a weekly review with my team calendar")
    assert len(t) <= 60
    assert "…" in t or len(t) < 57


def test_derive_strips_markdown():
    t = derive_session_title("## Budget\n\nPlease help track **Q3** spend")
    assert "##" not in t
    assert "Budget" in t or "help" in t.lower()


def test_weak_titles():
    assert title_is_weak("New Chat")
    assert title_is_weak("hi")
    assert title_is_weak("Hey!")
    assert not title_is_weak("Trip planning for December")


def test_assistant_prefix_stripped():
    t = derive_session_title("Sure! Here is a summary of your shopping list for the week.")
    assert not t.lower().startswith("sure!")


def test_code_block_not_in_title():
    t = derive_session_title('Fix this\n```python\nprint(1)\n```\nThanks')
    assert "print" not in t or "Fix" in t
