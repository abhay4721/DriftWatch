"""Provider-independent drift scan orchestration."""

from datetime import UTC, datetime
from pathlib import Path

from driftwatch.drift.adapters.base import ProviderAdapter
from driftwatch.drift.models import ScanReport


class DriftEngine:
    def __init__(self, adapter: ProviderAdapter) -> None:
        self.adapter = adapter

    def scan(self, source: Path) -> ScanReport:
        report = ScanReport(provider=self.adapter.name, source=str(source))
        findings = self.adapter.scan(source)
        report.findings = findings
        report.resource_count = getattr(
            self.adapter, "resource_count", len({finding.resource_address for finding in findings})
        )
        report.status = "drift" if findings else "clean"
        report.completed_at = datetime.now(UTC)
        return report
