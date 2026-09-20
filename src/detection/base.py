from __future__ import annotations

from typing import Protocol

from sentinel.domain.models import DetectionSnapshot


class DetectionSource(Protocol):
    def poll(self) -> DetectionSnapshot:
        """Return all signals active during one successful detection poll."""
