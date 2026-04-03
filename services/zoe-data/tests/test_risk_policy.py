from risk_policy import classify_request, is_whatsapp_connect_request
from routers.chat import _extract_approval_token


def test_high_risk_requires_confirmation():
    d = classify_request("Let's connect to WhatsApp and authorize the account")
    assert d.level == "high"
    assert d.requires_confirmation is True


def test_low_risk_read_only():
    d = classify_request("Show me status and list available tools")
    assert d.level == "low"
    assert d.requires_confirmation is False


def test_whatsapp_detection():
    assert is_whatsapp_connect_request("please connect whatsapp now")
    assert not is_whatsapp_connect_request("show my calendar")


def test_approval_token_parser():
    token, msg = _extract_approval_token("/approve abcdef1234 proceed")
    assert token == "abcdef1234"
    assert msg == "proceed"
