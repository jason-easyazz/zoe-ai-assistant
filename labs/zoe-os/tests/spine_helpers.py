"""A hand-written authorizer for spine tests that do not need the capability model."""
from __future__ import annotations

from zoe_os.events import Event, Visibility
from zoe_os.spine import Decision


class AllowList:
    """``origins`` may publish anything; ``readers[name]`` is the set of subjects whose
    events that reader may see (``"*"`` = everyone). Kernel events need ``"kernel"``."""

    def __init__(self, origins=(), skills=(), readers=None):
        self.origins, self.skills = set(origins), set(skills)
        self.readers: dict[str, set[str]] = readers or {}

    def is_origin(self, source):
        return source in self.origins

    def may_publish(self, source, event):
        return Decision(source in self.origins or source in self.skills, "unknown source")

    def may_subscribe(self, reader, kinds, subject):
        allowed = self.readers.get(reader, set())
        if subject and subject not in allowed and "*" not in allowed:
            return Decision(False, "no grant from that person")
        return Decision(reader in self.readers, "unknown reader")

    def may_forget(self, actor, subject):
        return Decision(actor == subject, "not theirs to forget")

    def may_read(self, reader, event: Event):
        allowed = self.readers.get(reader, set())
        if event.visibility is Visibility.KERNEL:
            return Decision("kernel" in allowed, "kernel visibility")
        return Decision("*" in allowed or event.subject in allowed, "not granted")
