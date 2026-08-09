"""Passcode `pattern=` constraints must stay ANCHORED.

pydantic 2.9.0 changed `Field(pattern=...)` from `re.match` to `re.search`.
Under `re.match` a pattern was implicitly anchored at the START, so a sloppy
`\\d+` still rejected "abc1234". Under `re.search` that same pattern matches
ANYWHERE in the string, so it silently starts ACCEPTING "abc1234" — a real
loosening of a security constraint (the passcode is a login credential).

Every passcode field in this service uses `^\\d+$`, which is anchored at both
ends and therefore behaves identically under both engines. These tests pin that
property against the REAL request models, so anyone who drops the `^`/`$` — or
adds a new passcode field without them — gets a red test instead of a quietly
weaker credential check.

Negative control: remove the `^` and `$` from any of the patterns below in the
source model and this file must go RED.
"""

import pytest
from pydantic import ValidationError

from api.admin import PasscodeSetRequest
from api.auth import PasscodeLoginRequest, PasscodeSetupRequest
from api.touch_panel import QuickSwitchRequest, TouchPanelAuthRequest

# (model, passcode field name, extra required fields for a valid instance)
PASSCODE_MODELS = [
    (PasscodeLoginRequest, "passcode", {"user_id": "jason"}),
    (PasscodeSetupRequest, "passcode", {}),
    (PasscodeSetRequest, "passcode", {}),
    (TouchPanelAuthRequest, "passcode", {"username": "jason", "device_id": "panel-1"}),
    (
        QuickSwitchRequest,
        "new_passcode",
        {"new_username": "jason", "device_id": "panel-1"},
    ),
]

MODEL_IDS = [m.__name__ for m, _, _ in PASSCODE_MODELS]

# Strings that contain a run of digits but are NOT wholly digits. An unanchored
# `\d+` under re.search accepts every one of these; `^\d+$` rejects all of them.
EMBEDDED_MATCHES = [
    "abc1234",  # digits at the end
    "1234abc",  # digits at the start
    "ab12cd",  # digits in the middle
    "12 34",  # internal whitespace
    "12\n34",  # embedded newline
    "1234\n",  # trailing newline — `$` in pydantic-core is end-of-TEXT
    "\n1234",  # leading newline
]


@pytest.mark.parametrize(
    ("model", "field", "extra"), PASSCODE_MODELS, ids=MODEL_IDS
)
@pytest.mark.parametrize("passcode", EMBEDDED_MATCHES)
def test_embedded_digit_run_is_rejected(model, field, extra, passcode):
    """A string that merely CONTAINS digits must not pass as a passcode."""
    with pytest.raises(ValidationError):
        model(**{field: passcode}, **extra)


@pytest.mark.parametrize(
    ("model", "field", "extra"), PASSCODE_MODELS, ids=MODEL_IDS
)
def test_all_digit_passcode_is_accepted(model, field, extra):
    """Positive control — the constraint must not reject legitimate passcodes.

    Without this, the test above would still pass if the pattern were changed to
    something that rejects everything.
    """
    instance = model(**{field: "1234"}, **extra)
    assert getattr(instance, field) == "1234"
