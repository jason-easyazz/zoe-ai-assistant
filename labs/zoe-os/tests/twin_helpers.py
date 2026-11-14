"""Load generated days into one lab kernel with the twin installed."""
from __future__ import annotations

from zoe_os.kernel import Kernel
from zoe_os.replay import join, load, play
from zoe_os.twin.baseline import Twin
from zoe_os.twin.days import SAMPLE, generate


def learn(tmp_path, days=None, *, n=28, seed=7, off=None, twin=None):
    """(kernel, twin, clock): ``n`` generated days (or the given ones) fed through the kernel."""
    days = days if days is not None else generate(SAMPLE, days=n, seed=seed)
    rec = join(days)
    rec.off = off or {}
    tmp_path.mkdir(parents=True, exist_ok=True)
    now = [rec.events[0]["t"]]
    k = Kernel(str(tmp_path / "twin.db"), rec.people, clock=lambda: now[0])
    k.path = str(tmp_path / "twin.db")
    twin = twin or Twin()
    load(k, rec, [twin])
    play(k, rec, now)
    return k, twin, now
