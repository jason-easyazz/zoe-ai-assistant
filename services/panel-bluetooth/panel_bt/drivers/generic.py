"""The fallback driver: connect / disconnect / forget plus the standard Device Information and Battery reads."""
from __future__ import annotations

from ..model import Advert
from .base import Driver


class GenericDriver(Driver):
    name = "generic"
    label = "Bluetooth device"
    known = False

    def match(self, advert: Advert) -> int:
        return 1
