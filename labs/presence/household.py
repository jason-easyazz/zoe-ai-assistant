"""Synthetic household traces with ground truth for the presence-fusion lab. Stdlib only, deterministic per seed.

Four placeholder people: ``adult_a`` (office job, wears the pendant, owns the PIN), ``adult_b`` (school run, some days out),
``child_c`` (school, no phone) and ``guest_g`` (an evening visitor, never registered). One panel, in the kitchen.

Every random draw is made once per seed, whatever sensors a scenario turns on, so two scenarios differ only in which
observation FAMILIES they keep (``Trace.observations(families)``).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

MIN = 60.0
DAY = 86400.0
DAY0 = 1_700_000_000.0 - (1_700_000_000.0 % DAY)   # a synthetic Monday-ish midnight; local time = UTC in the lab
ROOMS = ("kitchen", "living", "bed_a", "bed_c")
PANEL_ROOM = "kitchen"
CAMERA_ROOMS = ("kitchen", "living")
MEMBERS = ("adult_a", "adult_b", "child_c")
PHONES = ("adult_a", "adult_b")
AWAY = "away"

# Families: today's signals (no new hardware), the operator-configured phone tracker, and the hardware adapters.
TODAY = ("voice", "touch", "pin", "alarm", "pendant", "chat", "toggle", "tv")
PHONE = ("phone",)
HARDWARE = ("door", "ble", "mmwave", "camera")


@dataclass
class Trace:
    days: int
    seed: int
    truth: dict = field(default_factory=dict)          # person -> list[str] per minute (room name or "away")
    asleep: dict = field(default_factory=dict)         # person -> list[bool] per minute
    wakes: dict = field(default_factory=dict)          # person -> [epoch seconds of each day's wake]
    obs: list = field(default_factory=list)            # (t, family, kind, person, value, room[, count])

    def observations(self, families):
        keep = set(families)
        return [o for o in self.obs if o[1] in keep]


def _n(rng, mu, sd):
    return int(round(rng.gauss(mu, sd)))


def _day_plan(rng, d):
    """Minute-of-day plans: list of (start_min, place) per person, plus wake minutes and the day's quirks."""
    weekday = d % 7 < 5
    outing_b = weekday and d % 7 in (0, 2, 4)
    phone_left = d % 7 == 3                       # adult_a forgets the phone once a week
    plan, wake = {}, {}

    wa = _n(rng, 390, 15) if weekday else _n(rng, 480, 30)
    wake["adult_a"] = wa
    pa = [(0, "bed_a"), (wa + 15, "kitchen")]
    if weekday:
        leave, back = _n(rng, 465, 10), _n(rng, 1050, 30)
        pa += [(leave, AWAY), (back, "kitchen"), (back + 60, "living")]
    elif d % 7 == 5:
        leave, back = _n(rng, 600, 20), _n(rng, 720, 30)
        pa += [(leave, AWAY), (back, "living")]
    else:
        pa += [(_n(rng, 600, 30), "living")]
    pa += [(_n(rng, 1350, 20), "bed_a")]
    plan["adult_a"] = pa

    wb = _n(rng, 420, 20)
    wake["adult_b"] = wb
    pb = [(0, "bed_a"), (wb + 10, "kitchen")]
    school_out, school_in = _n(rng, 495, 5), _n(rng, 930, 5)
    if weekday:
        pb += [(school_out, AWAY)]
        if outing_b:
            pb += [(school_in, "kitchen")]                               # comes home WITH the child
        else:
            pb += [(school_out + _n(rng, 40, 5), "kitchen"), (school_in - 20, AWAY), (school_in, "kitchen")]
    t = (school_in if weekday else wb + 60) + 45
    while t < 1380:
        pb.append((t, "living" if len(pb) % 2 else "kitchen"))
        t += _n(rng, 50, 10)
    pb += [(_n(rng, 1380, 15), "bed_a")]
    plan["adult_b"] = pb

    wc = _n(rng, 435, 10) if weekday else _n(rng, 500, 20)
    wake["child_c"] = wc
    pc = [(0, "bed_c"), (wc + 15, "kitchen")]
    if weekday:
        pc += [(school_out, AWAY), (school_in, "kitchen"), (school_in + 30, "living"), (school_in + 120, "bed_c")]
    elif d % 7 == 5:
        pc += [(pa[2][0], AWAY), (pa[3][0], "living")]
    else:
        pc += [(wc + 90, "living")]
    pc += [(_n(rng, 1200, 10), "bed_c")]
    plan["child_c"] = pc

    plan["guest_g"] = [(0, AWAY)] + ([(1080, "living"), (1260, AWAY)] if d % 7 == 2 else [])
    sleep = {"adult_a": (pa[-1][0], wa), "adult_b": (pb[-1][0], wb), "child_c": (pc[-1][0], wc)}
    return plan, wake, sleep, phone_left


def _expand(plan, minutes=1440):
    out, cur, i = [], plan[0][1], 0
    plan = sorted(plan, key=lambda x: x[0])
    for m in range(minutes):
        while i < len(plan) and plan[i][0] <= m:
            cur = plan[i][1]
            i += 1
        out.append(cur)
    return out


def generate(days: int = 28, seed: int = 1) -> Trace:
    rng = random.Random(seed)
    tr = Trace(days, seed)
    people = MEMBERS + ("guest_g",)
    for p in people:
        tr.truth[p], tr.asleep[p] = [], []
    for p in MEMBERS:
        tr.wakes[p] = []
    phone_left_days = set()
    for d in range(days):
        plan, wake, sleep, phone_left = _day_plan(rng, d)
        if phone_left:
            phone_left_days.add(d)
        for p in people:
            tr.truth[p] += _expand(plan[p])
        for p in MEMBERS:
            bed, up = sleep[p]
            tr.asleep[p] += [m < up or m >= bed for m in range(1440)]
            tr.wakes[p].append(DAY0 + d * DAY + wake[p] * MIN)

    obs = tr.obs
    wifi_drop = {p: 0 for p in PHONES}
    ble_miss = {p: 0 for p in PHONES}
    phone_where = {p: "home" for p in PHONES}
    phone_lag = {p: 0 for p in PHONES}
    total = days * 1440
    for m in range(total):
        t = DAY0 + m * MIN
        d, mod = divmod(m, 1440)
        evening = mod >= 1050 or mod < 450
        for p in MEMBERS:
            here = tr.truth[p][m]
            awake = not tr.asleep[p][m]
            prev = tr.truth[p][m - 1] if m else here
            if (prev == AWAY) != (here == AWAY) and rng.random() > 0.02:
                obs.append((t, "door", "door_contact", None, "home", None))
            if here == AWAY:
                if awake and rng.random() < 0.003:
                    obs.append((t, "chat", "member_chat", p, "home", None))
                continue
            if awake and here == PANEL_ROOM:
                rate = {"adult_a": 0.04, "adult_b": 0.03, "child_c": 0.02}[p]
                if rng.random() < rate:
                    ok = rng.random() < (0.9 if p != "child_c" else 0.8)
                    obs.append((t, "voice", "voice_verified" if ok else "someone_voice", p if ok else None, "home", PANEL_ROOM))
                if p != "child_c" and rng.random() < 0.01:
                    obs.append((t, "touch", "panel_touch_member", p, "home", PANEL_ROOM))
                if p == "adult_a" and rng.random() < 0.003:
                    obs.append((t, "pin", "pin_unlock", p, "home", PANEL_ROOM))
            if awake and rng.random() < 0.003:
                obs.append((t, "chat", "member_chat", p, "home", None))
            if p == "adult_a" and mod % 2 == 0 and rng.random() < 0.6:
                obs.append((t, "pendant", "pendant_sync", p, "home", None))
            if awake and evening and rng.random() < 0.02:
                obs.append((t, "toggle", "room_toggle", None, "home", here))
            if rng.random() < 0.95:
                obs.append((t, "mmwave", "mmwave", None, "home", here))
            if here in CAMERA_ROOMS and awake and rng.random() < 0.9 * 0.15:
                obs.append((t, "camera", "face_match", p, "home", here))
        # the weekday alarm on the panel, dismissed a minute or two after waking
        for p in ("adult_a",):
            for w in tr.wakes[p]:
                if t <= w + 2 * MIN < t + MIN and d % 7 < 5:
                    obs.append((t, "alarm", "alarm_dismissed", p, "home", None))
        # the guest: an unverified voice that carries to the kitchen panel now and then (open plan), room occupancy
        g = tr.truth["guest_g"][m]
        if g != AWAY:
            if rng.random() < 0.02:
                obs.append((t, "voice", "someone_voice", None, "home", PANEL_ROOM))
            if rng.random() < 0.95:
                obs.append((t, "mmwave", "mmwave", None, "home", g))
        # person-detect counts the people in each camera room (it may miss one in a group)
        for r in CAMERA_ROOMS:
            n = sum(1 for q in people if tr.truth[q][m] == r)
            if n and rng.random() < 0.9:
                if n >= 2 and rng.random() < 0.1:
                    n -= 1
                obs.append((t, "camera", "camera_person", None, "home", r, n))
        # the TV: false wakes at the panel 19:00-21:30; one in a hundred is falsely accepted as an adult
        if 1140 <= mod < 1290 and rng.random() < 0.02:
            if rng.random() < 0.01:
                obs.append((t, "tv", "voice_verified", rng.choice(PHONES), "home", PANEL_ROOM))
            else:
                obs.append((t, "tv", "someone_voice", None, "home", PANEL_ROOM))
        # spurious door (bins, deliveries) once a day or so, false mmWave
        if rng.random() < 1 / 900:
            obs.append((t, "door", "door_contact", None, "home", None))
        for r in ROOMS:
            if rng.random() < 0.001:
                obs.append((t, "mmwave", "mmwave", None, "home", r))
        # phones: with the person unless left at home; the HA tracker reports away 3 min after leaving / a Wi-Fi drop
        for p in PHONES:
            person_here = tr.truth[p][m]
            left_home = d in phone_left_days and p == "adult_a"
            at_home = person_here != AWAY or left_home
            asleep = tr.asleep[p][m]
            if at_home and wifi_drop[p] == 0 and rng.random() < (0.004 if asleep else 0.0015):
                wifi_drop[p] = rng.randint(4, 30) if asleep else rng.randint(3, 12)
            on_wifi = at_home and wifi_drop[p] == 0
            if wifi_drop[p]:
                wifi_drop[p] -= 1
            phone_lag[p] = 0 if on_wifi else phone_lag[p] + 1
            reported = "home" if (on_wifi or phone_lag[p] < 3) else "away"
            phone_where[p] = reported
            obs.append((t, "phone", "phone_tracker", p, reported, None))
            # BLE IRK: per-room scanners see the phone (not the Wi-Fi), 90 % per minute; 3 misses in a row = away
            phone_room = (PANEL_ROOM if (left_home and person_here == AWAY) else person_here) if at_home else None
            if phone_room is not None and rng.random() < 0.9:
                ble_miss[p] = 0
                obs.append((t, "ble", "ble_irk", p, "home", phone_room))
            else:
                ble_miss[p] += 1
                if ble_miss[p] >= 3:
                    obs.append((t, "ble", "ble_irk", p, "away", None))
    obs.sort(key=lambda o: o[0])
    tr.phone_left_days = sorted(phone_left_days)
    return tr
