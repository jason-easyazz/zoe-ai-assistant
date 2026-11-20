"""The home-control area of the door: the kernel decides and records a member's command to the house (a light, a switch, the
heating, a cover, a lock, a scene, a media player). Nothing is stored for it: the command goes to Home Assistant, sent by
production. No device name, room name or scene name reaches this process: the verb, who asked, and the entity id.

THE VERB CARRIES THE CLASS OF THING AND WHAT IS DONE TO IT, both closed codes: ``<class>.<act>`` with class one of ``light``,
``switch``, ``climate``, ``cover``, ``lock``, ``scene``, ``media``, ``other`` and act one of ``on``, ``off``, ``set``, ``open``,
``close``. The kernel decides by class, never by device.

    who                                  what
    an adult                             everything
    a child                              only the classes their LEVEL allows (below); else refuse adults_only
    a guest on the roster                as the shared panel
    the shared panel (nobody identified) everything EXCEPT opening: a lock or a cover that is not being closed -> adults_only
    not on the roster                    refuse unknown_person

A CHILD'S LEVEL is a set of classes. It is stated per child, beside the roster (``home-levels.json``, read by ``levels``), as a
level's name or as a plain list of classes; a child with nothing stated has ``DEFAULT_LEVEL``:

    level      a child may control
    none       nothing
    lights     lights, and media players (music is open to everyone)            <- the default
    comfort    lights, media players, switches and plugs, scenes
    most       all of ``comfort``, and the heating / cooling
    (a list)   exactly the classes listed: the only way a child is given covers or locks

Locks and covers (a garage door is a cover) are in no named level: an adult has to list them for a child on purpose.

SETTING A LEVEL is a change like any other (``level.set``; the child is the ``owner`` of the ask): an adult only.

TWO SAFETY RULES LIVE IN PRODUCTION, and the verbs are shaped so it can keep them without knowing a device's name:
  * turning a light or a media player OFF (``light.off``, ``media.off``) is never held up: it is made first and told afterwards;
  * anything that could OPEN the house (class ``lock`` or ``cover`` with any act but ``close``) is ALWAYS asked first, and a
    kernel that cannot be reached means NO. It is the one place where "kernel down" refuses instead of proceeding.
"""
from __future__ import annotations

from typing import Callable

from .capability import ADULT, CHILD
from .door import ADULTS_ONLY, PANEL, PRIVATE, UNKNOWN_PERSON, Area
from .spine import Decision

CLASSES = ("light", "switch", "climate", "cover", "lock", "scene", "media", "other")
ACTS = ("on", "off", "set", "open", "close")
SET_LEVEL = "level.set"
VERBS = (*(f"{c}.{a}" for c in CLASSES for a in ACTS), SET_LEVEL)
SECURE_CLASSES = ("lock", "cover")
LEVELS = {
    "none": frozenset(),
    "lights": frozenset({"light", "media"}),
    "comfort": frozenset({"light", "media", "switch", "scene"}),
    "most": frozenset({"light", "media", "switch", "scene", "climate"}),
}
DEFAULT_LEVEL = "lights"
#: ``() -> {child id: level name | list of classes}``: what is stated beside the roster. The daemon installs the file's reader.
levels: Callable[[], dict] = dict


LEVELS_FILE = "home-levels.json"      # beside people.json, 0600: {"levels": {"<child id>": "<level name>" | [classes]}}


def file_levels(path) -> Callable[[], dict]:
    """A reader of the levels file that follows the file with no restart (read again when its inode, size or time changes).
    A file that is missing, cannot be read, is not that shape, or that anyone but its owner can read or write, states NOTHING:
    every child then has the default level, never more."""
    import json
    import os

    seen: list = [None, {}]

    def read() -> dict:
        try:
            st = os.stat(path)
        except OSError:
            seen[:] = [None, {}]
            return seen[1]
        ident = (st.st_ino, st.st_size, st.st_mtime_ns)
        if ident != seen[0]:
            stated: dict = {}
            try:
                if st.st_mode & 0o077 == 0:
                    with open(path, "rb") as fh:
                        raw = json.loads(fh.read(65536)).get("levels")
                    if isinstance(raw, dict):
                        stated = {k: v for k, v in raw.items() if isinstance(k, str)}
            except (OSError, ValueError, AttributeError):
                stated = {}
            seen[:] = [ident, stated]
        return seen[1]

    return read


def opens(verb: str) -> bool:
    """Could this open the house? A lock or a cover, and anything but closing it."""
    cls, _, act = verb.partition(".")
    return cls in SECURE_CLASSES and act != "close"


def allowed_classes(stated) -> frozenset:
    """A child's classes from what was stated for them: a level's name, a list of classes, or nothing (the default level).
    Anything else (an unknown name, a list holding an unknown class, another type) is the DEFAULT, never more."""
    if isinstance(stated, str) and stated in LEVELS:
        return LEVELS[stated]
    if isinstance(stated, (list, tuple)) and all(isinstance(c, str) and c in CLASSES for c in stated):
        return frozenset(stated)
    return LEVELS[DEFAULT_LEVEL]


def decide(people: dict, verb: str, actor: str, *, via: str | None = None, owner: str | None = None,
           visibility: str = PRIVATE) -> Decision:
    """The whole permission table (module docstring), one rule per line. ``people`` is the kernel's roster."""
    panel = via == PANEL
    who = None if panel else people.get(actor)
    if not panel and who is None:
        return Decision(False, UNKNOWN_PERSON)
    if who is not None and who.role == ADULT:                      # an adult: everything, a child's level included
        return Decision(True)
    if verb == SET_LEVEL:                                          # only an adult states what a child may control
        return Decision(False, ADULTS_ONLY)
    if who is not None and who.role == CHILD:                      # a child: the classes their level allows
        cls = verb.partition(".")[0]
        return Decision(True) if cls in allowed_classes(levels().get(actor)) else Decision(False, ADULTS_ONLY)
    return Decision(False, ADULTS_ONLY) if opens(verb) else Decision(True)      # the panel, a guest: anything but opening


AREA = Area("home", VERBS, decide, target="entity")
