"""The driver registry: an advert (name, service UUID, manufacturer data) -> the best driver for it."""
from __future__ import annotations

from typing import Iterable, Optional

from ..model import Advert
from .base import Driver
from .generic import GenericDriver
from .omi import OmiDriver


class DriverRegistry:
    def __init__(self, drivers: Optional[Iterable[Driver]] = None) -> None:
        self._drivers: list[Driver] = list(drivers) if drivers is not None else [OmiDriver(), GenericDriver()]
        if not any(isinstance(d, GenericDriver) for d in self._drivers):
            self._drivers.append(GenericDriver())          # every device must stay connectable

    def identify(self, advert: Advert) -> Driver:
        """Highest score wins; ties go to the earlier-registered driver. Always returns something (the generic fallback)."""
        best, best_score = self._drivers[-1], 0
        for d in self._drivers:
            score = d.match(advert)
            if score > best_score:
                best, best_score = d, score
        return best

    def by_name(self, name: str) -> Optional[Driver]:
        return next((d for d in self._drivers if d.name == name), None)

    def names(self) -> list[str]:
        return [d.name for d in self._drivers]


__all__ = ["DriverRegistry", "Driver", "GenericDriver", "OmiDriver"]
