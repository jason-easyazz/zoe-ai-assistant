"""
Conversation Reflector
=======================

Phase 2: Scans conversations for correction signals and extracts learnings.

High confidence patterns (v1 - enabled at launch):
- "no, I meant..."
- "that's wrong, it should be..."
- "never do that"
- "always use"
- "actually it's..."
- "I told you..."
- "remember that..."
- "don't forget..."

Medium confidence patterns (v2 - disabled at launch):
- Rephrasing
- Alternatives

Security: Trust Gate integration prevents untrusted sources from creating learnings.
"""

import re
import logging
from typing import List, Optional, Dict, Any
from dataclasses import dataclass
from datetime import datetime

logger = logging.getLogger(__name__)

# High confidence correction patterns (v1)
HIGH_CONFIDENCE_PATTERNS = [
    (r"\bno[,.]?\s*i\s+meant\b", "correction"),
    (r"\bthat'?s?\s+wrong\b", "correction"),
    (r"\bit\s+should\s+be\b", "correction"),
    (r"\bnever\s+do\s+that\b", "preference"),
    (r"\balways\s+use\b", "preference"),
    (r"\bactually\s+it'?s?\b", "correction"),
    (r"\bi\s+told\s+you\b", "reminder"),
    (r"\bremember\s+that\b", "fact"),
    (r"\bdon'?t\s+forget\b", "fact"),
    (r"\bnot\s+like\s+that\b", "correction"),
    (r"\bthat'?s?\s+not\s+(right|correct)\b", "correction"),
    (r"\bi\s+prefer\b", "preference"),
    (r"\bi\s+don'?t\s+like\b", "preference"),
    (r"\bi\s+hate\s+when\b", "preference"),
    (r"\bplease\s+don'?t\b", "preference"),
    (r"\bstop\s+doing\s+that\b", "preference"),
    (r"\bnext\s+time[,]?\s+", "instruction"),
    (r"\bfrom\s+now\s+on\b", "instruction"),
]


@dataclass
class Learning:
    """A detected learning from a conversation."""
    category: str           # correction, preference, fact, instruction, reminder
    content: str            # The learning content
    context: str            # Surrounding conversation context
    source_message: str     # The user message that triggered detection
    confidence: float       # 0.0-1.0 confidence score
    user_id: str            # Which user this learning belongs to
    trust_level: str        # "owner", "trusted", "untrusted"
    status: str             # "confirmed", "pending_review", "rejected"
    detected_at: str        # ISO timestamp


class ConversationReflector:
    """Scan conversations for corrections and extract learnings."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._pending_learnings: List[Learning] = []

    def scan_message(
        self,
        user_message: str,
        assistant_response: str,
        user_id: str,
        trust_mode: str = "ACT",
        is_owner: bool = True,
    ) -> List[Learning]:
        """Scan a user message for correction signals.

        Args:
            user_message: The user's message
            assistant_response: Zoe's previous response (for context)
            user_id: The user who sent the message
            trust_mode: "ACT" or "READ" from Trust Gate
            is_owner: Whether this is the primary Zoe account owner

        Returns:
            List of detected learnings
        """
        if not self.enabled:
            return []

        # Trust Gate integration: untrusted sources cannot create learnings
        if trust_mode == "READ":
            logger.debug(f"Skipping learning detection for untrusted source (user: {user_id})")
            return []

        learnings = []
        message_lower = user_message.lower()

        for pattern, category in HIGH_CONFIDENCE_PATTERNS:
            match = re.search(pattern, message_lower, re.IGNORECASE)
            if match:
                # Determine trust level and initial status
                if is_owner:
                    trust_level = "owner"
                    status = "confirmed"
                else:
                    trust_level = "trusted"
                    status = "pending_review"

                learning = Learning(
                    category=category,
                    content=user_message,
                    context=f"Zoe said: {assistant_response[:200]}...\nUser replied: {user_message}",
                    source_message=user_message,
                    confidence=0.85,
                    user_id=user_id,
                    trust_level=trust_level,
                    status=status,
                    detected_at=datetime.utcnow().isoformat() + "Z",
                )
                learnings.append(learning)
                logger.info(
                    f"Learning detected: category={category}, "
                    f"status={status}, user={user_id}, "
                    f"pattern='{pattern}'"
                )
                break  # One learning per message to avoid noise

        return learnings

    def get_pending_learnings(self, user_id: str) -> List[Learning]:
        """Get learnings pending review for a user."""
        return [l for l in self._pending_learnings if l.user_id == user_id and l.status == "pending_review"]


# Singleton
reflector = ConversationReflector()
