"""A child's HOME-CONTROL LEVEL: what an adult lets each child control in the house (``ZOE_OS_HOME=kernel``).

A level is a set of CLASSES of thing (never a device): ``light``, ``switch``, ``climate``, ``cover``, ``lock``, ``scene``,
``media``, ``other``. It is stated per child in ``home-levels.json`` beside the kernel's roster (``ZOE_OS_SHADOW_DIR``, 0600),
as a level's name (``none``, ``lights``, ``comfort``, ``most``) or a plain list of classes. The KERNEL reads that file and
decides with it (``labs/zoe-os/zoe_os/home_door.py``); zoe-data only writes it. A child with nothing stated has ``lights``.

SETTING a level is a change like any other: asked first (``level.set``), an adult only. With the door on, a kernel that cannot
be asked means the level is NOT changed. With the door off the file is written as the roster tool writes its own (an operator
on the box).

There is no settings screen yet. The command line is the setter:

    python3 home_level.py --by <adult member id> <child id>=<level>      e.g.  member-kip=comfort   member-kip=light,switch
    python3 home_level.py --show
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

FILE = "home-levels.json"
CLASSES = ("light", "switch", "climate", "cover", "lock", "scene", "media", "other")
NAMED = ("none", "lights", "comfort", "most")
_ID = re.compile(r"[A-Za-z0-9._:@_-]{1,80}\Z")


class LevelError(ValueError):
    """The level, or the id, is not one that can be stated."""


def parse(level: Any):
    """A level as it is stored: a known name, or a de-duplicated list of known classes. Anything else raises ``LevelError``."""
    if isinstance(level, str) and level in NAMED:
        return level
    parts = [p.strip() for p in level.split(",")] if isinstance(level, str) else list(level or [])
    if not parts or not all(isinstance(p, str) and p in CLASSES for p in parts):
        raise LevelError("a level is one of " + ", ".join(NAMED) + ", or a list of " + ", ".join(CLASSES))
    return sorted(set(parts))


def path() -> Path:
    import os_shadow

    return Path(os_shadow.state_dir()) / FILE


def read(where: Optional[Path] = None) -> dict:
    """What is stated now (empty when there is no file, or it cannot be read)."""
    try:
        raw = json.loads((where or path()).read_text("utf-8")).get("levels")
        return {k: v for k, v in raw.items() if isinstance(k, str)} if isinstance(raw, dict) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def _write(levels: dict, where: Path) -> None:
    """Atomic, 0600: a unique temp file in the same directory, fsync, rename."""
    fd, tmp = tempfile.mkstemp(dir=str(where.parent), prefix=".home-levels-")
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"levels": levels}, fh, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, where)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


async def set_level(actor: str, child: str, level: Any, *, where: Optional[Path] = None) -> Any:
    """State ``child``'s level, as ``actor``. Asked first when the home door is on: a refusal raises
    ``action_doors.HomeRefused`` (``adults_only``: only an adult sets a level), and a kernel that could not be asked raises it
    too (``unavailable``): a level is never changed unchecked. Returns the level as stored."""
    import action_doors
    import kernel_door
    import os_shadow

    stamps_at = kernel_door.stamps_now("home")
    if not (isinstance(child, str) and _ID.match(child)) or not (isinstance(actor, str) and _ID.match(actor)):
        raise LevelError("not a member id")
    stored = parse(level)
    asked = None
    if stamps_at is not None:
        decision, reason, ask, stamps = await os_shadow.ask_door("home", "level.set", actor, target=None, owner=child, household=False,
                                                                 stamps_at=stamps_at)
        if decision != "allow":
            raise action_doors.HomeRefused(reason if decision == "refuse" else "unavailable")
        asked = kernel_door.Decision(decision, reason, ask, stamps=stamps, area="home")
    where = where or path()
    try:
        levels = read(where)
        levels[child] = stored
        _write(levels, where)
    except BaseException:
        kernel_door.wrote(asked, "failed")
        raise
    kernel_door.wrote(asked)
    return stored


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description="State what a child may control in the house (a home-control level).")
    ap.add_argument("--by", help="the adult member stating it")
    ap.add_argument("--show", action="store_true", help="print what is stated")
    ap.add_argument("pairs", nargs="*", help="<child id>=<level>")
    args = ap.parse_args(argv)
    if args.show or not args.pairs:
        print(json.dumps(read(), indent=1, sort_keys=True))
        return 0
    if not args.by:
        print("--by <adult member id> is required to change a level", file=sys.stderr)
        return 2
    for pair in args.pairs:
        child, _, level = pair.partition("=")
        try:
            stored = asyncio.run(set_level(args.by, child.strip(), level.strip()))
        except Exception as exc:  # noqa: BLE001 - a refusal or a bad level: say which, change nothing
            print(f"{child}: not changed ({getattr(exc, 'reason', None) or exc})", file=sys.stderr)
            return 1
        print(f"{child}: {stored}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
