import re
from dataclasses import dataclass


@dataclass
class RiskDecision:
    level: str
    requires_confirmation: bool
    reason: str
    normalized_action: str


HIGH_RISK_PATTERNS = [
    r"\b(delete|remove|drop|destroy|wipe|truncate)\b",
    r"\b(connect|link|authorize)\b.*\b(whatsapp|oauth|account|token)\b",
    r"\b(docker|container)\b.*\b(restart|stop|rm|remove|prune)\b",
    r"\b(force|override|disable security)\b",
    r"\b(run|execute)\b.*\b(shell|bash|terminal)\b",
]

MEDIUM_RISK_PATTERNS = [
    r"\b(create|update|write|send|post|schedule)\b",
    r"\b(enable|configure|setup|install)\b",
]

LOW_RISK_PATTERNS = [
    r"\b(show|list|search|find|status|what can you do|help)\b",
    r"\b(read|view|check)\b",
]


def classify_request(message: str) -> RiskDecision:
    text = (message or "").strip().lower()
    normalized = re.sub(r"\s+", " ", text)
    for pat in HIGH_RISK_PATTERNS:
        if re.search(pat, normalized):
            return RiskDecision(
                level="high",
                requires_confirmation=True,
                reason="Potentially destructive or external-account action",
                normalized_action=normalized,
            )
    for pat in MEDIUM_RISK_PATTERNS:
        if re.search(pat, normalized):
            return RiskDecision(
                level="medium",
                requires_confirmation=True,
                reason="State-changing action",
                normalized_action=normalized,
            )
    for pat in LOW_RISK_PATTERNS:
        if re.search(pat, normalized):
            return RiskDecision(
                level="low",
                requires_confirmation=False,
                reason="Read-only or informational request",
                normalized_action=normalized,
            )
    return RiskDecision(
        level="low",
        requires_confirmation=False,
        reason="No risky pattern detected",
        normalized_action=normalized,
    )


def is_whatsapp_connect_request(message: str) -> bool:
    text = (message or "").lower()
    return "whatsapp" in text and any(k in text for k in ["connect", "link", "setup", "configure"])
