from __future__ import annotations
import re, time
from dataclasses import dataclass, field
from typing import ClassVar, Optional

_PERCENTAGE_RE = re.compile(r'\b(\d{1,3})\s*(?:percent|%)\b|(?:^|\s)(\d{1,3})(?:\s|$)', re.I)
_DIRECTION_RE  = re.compile(r'\b(up|louder|higher|down|quieter|lower|softer)\b', re.I)

@dataclass
class ConversationContext:
    last_intent: str | None = None
    last_slots:  dict       = field(default_factory=dict)
    last_text:   str | None = None
    updated_at:  float      = field(default_factory=time.time)
    TTL: ClassVar[float]    = 120.0  # 2-minute window

    def is_fresh(self) -> bool:
        return (time.time() - self.updated_at) < self.TTL

    def activate(self, intent_name: str, slots: dict, text: str) -> None:
        self.last_intent = intent_name
        self.last_slots  = dict(slots)
        self.last_text   = text
        self.updated_at  = time.time()

    def invalidate(self) -> None:
        self.last_intent = None

    def resolve_coreference(self, text: str) -> tuple[str | None, dict | None]:
        """
        Try to resolve an ambiguous utterance relative to the last intent.
        Returns (intent_name, slots) or (None, None).
        """
        if not self.is_fresh() or not self.last_intent:
            return None, None
        if self.last_intent == "set_volume":
            m = _PERCENTAGE_RE.search(text)
            if m:
                return "set_volume", {"level": int(m.group(1) or m.group(2))}
            d = _DIRECTION_RE.search(text)
            if d:
                return "set_volume", {"direction": d.group(1).lower()}
        if self.last_intent in ("music_play", "music_control", "music_volume"):
            m = _PERCENTAGE_RE.search(text)
            if m:
                return "music_volume", {"level": int(m.group(1) or m.group(2))}
        return None, None
