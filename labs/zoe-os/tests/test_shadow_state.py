"""The kernel's gate on its state directory (``zoe_os/shadow_state.py``): a new directory settles before it holds data, and a
directory copied in by hand is refused. The full table of cases, run against this file AND zoe-data's copy of the same text, is
``services/zoe-data/tests/test_os_shadow_settle.py``; these are the lab lane's own."""
import json

import pytest

from zoe_os import shadow_state as ss


class Clock:
    def __init__(self):
        self.wall, self.mono, self.slept, self.during = 1_800_000_000.0, 5_000.0, 0.0, None

    def now(self):
        return self.wall, self.mono, "boot-a"

    def sleep(self, s):
        if self.during:
            self.during()
        self.wall, self.mono, self.slept = self.wall + s, self.mono + s, self.slept + s


def test_a_new_state_directory_waits_the_settle_time_with_no_data_in_it(tmp_path):
    d, c, seen = tmp_path / "state", Clock(), []
    c.during = lambda: seen.append(ss.has_data(str(d)))
    assert ss.require_settled(d, c.now, c.sleep) >= ss.SETTLE_S > ss.TRUST_S > ss.POLL_S
    assert seen and not any(seen) and ss.read_marker(str(d)) is not None
    (d / "people.json").write_text(json.dumps({"people": []}))
    assert ss.require_settled(d, c.now, c.sleep) == 0.0            # settled, with data: served


@pytest.mark.parametrize("marker", [None, "{not json", "young"])
def test_data_with_no_trustworthy_marker_is_refused_and_left_untouched(tmp_path, marker):
    d, c = tmp_path / "copied", Clock()
    (d / "kernel").mkdir(parents=True)
    (d / "kernel" / "zoe-os.db").write_bytes(b"recorded")
    if marker == "young":
        ss.write_marker(str(d), *c.now(), ss.SETTLE_S)
    elif marker:
        (d / ss.MARKER).write_text(marker)
    with pytest.raises(SystemExit) as stop:
        ss.require_settled(d, c.now, c.sleep)
    assert "refusing to start" in str(stop.value) and (d / "kernel" / "zoe-os.db").read_bytes() == b"recorded"
