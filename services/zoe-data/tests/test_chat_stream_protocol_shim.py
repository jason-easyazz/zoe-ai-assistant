"""W4-C2 re-export contract: routers.chat re-exports chat_stream_protocol's
symbols as the SAME objects.

The twelve names below are PERMANENT API on routers.chat (the voice_tts
re-export contract, applied to chat): existing importers and monkeypatches
target routers.chat and must keep working.

NOT ci_safe on purpose: importing routers.chat pulls the heavy service stack,
so this runs only on the self-hosted full-dir lane (tests/AGENTS.md).
"""

_REEXPORTED = [
    "brain_tool_sentinel_events",
    "brain_tool_card_events",
    "_iter_openclaw_heartbeats",
    "_cancel_if_pending",
    "_BUILDER_INTENTS",
    "_detect_preview_urls",
    "_synthesize_builder_actions",
    "_sanitize_builder_reply",
    "_extract_ui_actions",
    "_map_ui_payload_to_action",
    "_queue_ui_actions_background",
    "_stream_openclaw_assistant_ag",
]


def test_chat_reexports_stream_protocol_symbols():
    import chat_stream_protocol
    from routers import chat

    for name in _REEXPORTED:
        assert getattr(chat, name) is getattr(chat_stream_protocol, name), (
            f"routers.chat.{name} is not the chat_stream_protocol object — "
            "the W4-C2 re-export shim broke"
        )
