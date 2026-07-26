"""W4-C3 re-export contract: routers.chat re-exports chat_hermes_stream's
callables as the SAME objects.

The six names below are PERMANENT API on routers.chat (the voice_tts
re-export contract, applied to chat): existing importers and monkeypatches
target routers.chat and must keep working. The _HERMES_* / _ZOE_SOUL_HERMES
constants are deliberately NOT re-exported (chat-split plan, W4-C3).

NOT ci_safe on purpose: importing routers.chat pulls the heavy service stack,
so this runs only on the self-hosted full-dir lane (tests/AGENTS.md).
"""

_REEXPORTED = [
    "_build_hermes_payload",
    "_hermes_progress_message",
    "_hermes_progress_events",
    "_hermes_request_headers",
    "_iter_hermes_stream_events",
    "_hermes_completion",
]

_NOT_REEXPORTED = [
    "_HERMES_API_URL",
    "_HERMES_MODEL",
    "_HERMES_API_KEY",
    "_ZOE_SOUL_HERMES",
]


def test_chat_reexports_hermes_stream_symbols():
    import chat_hermes_stream
    from routers import chat

    for name in _REEXPORTED:
        assert getattr(chat, name) is getattr(chat_hermes_stream, name), (
            f"routers.chat.{name} is not the chat_hermes_stream object — "
            "the W4-C3 re-export shim broke"
        )


def test_chat_does_not_reexport_hermes_constants():
    from routers import chat

    for name in _NOT_REEXPORTED:
        assert not hasattr(chat, name), (
            f"routers.chat.{name} exists — W4-C3 deliberately does not "
            "re-export Hermes constants"
        )
