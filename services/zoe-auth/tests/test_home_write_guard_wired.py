"""The zoe-auth lane runs under the home-write guard (tools/ci/home_write_guard.py; mechanism tests live in
tests/unit/test_home_write_guard.py)."""
import sys


def test_the_zoe_auth_lane_installed_the_guard_and_its_fixture_is_active(request):
    guard = sys.modules.get("zoe_home_write_guard")
    assert guard is not None and guard._state().enabled and guard._state().checkouts
    assert "_no_writes_under_the_real_home_or_the_checkout" in request.fixturenames
