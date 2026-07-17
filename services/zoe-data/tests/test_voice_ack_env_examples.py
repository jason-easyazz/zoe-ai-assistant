import pytest
from pathlib import Path

pytestmark = pytest.mark.ci_safe


ROOT = Path(__file__).resolve().parents[3]


def _comment_value(path: Path, key: str) -> str:
    """Return the value from the first commented '# KEY=value' line.

    Only reliable for keys with a single entry, such as
    ZOE_PROCESSING_ACK_PHRASES. Keys that appear twice as example plus empty
    placeholder should be checked with raw text substring matching instead.
    """
    prefix = f"# {key}="
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(prefix):
            return line.removeprefix(prefix).strip()
    raise AssertionError(f"missing {key} in {path}")


def test_pipe_separated_ack_examples_are_shell_safe_when_copied_to_env():
    example = ROOT / "services" / "zoe-data" / ".env.example"

    for key in ("ZOE_PROCESSING_ACK_PHRASES",):
        value = _comment_value(example, key)
        assert "|" in value
        assert value.startswith('"') and value.endswith('"')

    text = example.read_text(encoding="utf-8")
    assert 'ZOE_WAKE_ACK_PHRASES="Yes Jason?|Good morning Jason.|Good evening Jason."' in text
    assert 'ZOE_WAKE_ACK_VARIANT_LABELS="default|morning|evening"' in text


def test_pi_voice_daemon_template_warns_to_quote_phrase_bank():
    text = (ROOT / "scripts" / "setup" / "pi_voice_daemon_install.sh").read_text(encoding="utf-8")

    assert 'ZOE_WAKE_ACK_PHRASES="Yes Jason.|Hi Jason.|Good morning Jason."' in text
    assert "Quote pipe-separated values" in text
