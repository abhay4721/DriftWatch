"""Provider adapter contract for scan backends."""

from abc import ABC, abstractmethod
from pathlib import Path

from driftwatch.drift.models import DriftFinding


class ProviderAdapter(ABC):
    name: str

    @abstractmethod
    def scan(self, source: Path) -> list[DriftFinding]:
        """Inspect a configured infrastructure source without changing it."""
