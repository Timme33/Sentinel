from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from sentinel.domain.investigation import EvidenceItem, InvestigationJob
from sentinel.domain.models import Incident, IncidentTrigger
from sentinel.investigation.evidence_service import EvidenceService


START = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


class FakeRepository:
    def __init__(self):
        self.items = []

    def add_evidence(self, job_id, kind, source, summary, payload, created_at):
        item = EvidenceItem(
            f"E{len(self.items) + 1}", job_id, kind, source, summary, payload, created_at
        )
        self.items.append(item)
        return item

    def list_evidence(self, job_id):
        return tuple(self.items)


class FakeJaeger:
    def __init__(self):
        self.searches = []

    def search_traces(self, service, start, end, operation, limit):
        self.searches.append((service, operation))
        return ()


class FakeOpenSearch:
    def search_logs(self, *args, **kwargs):
        return ()

    def logs_for_trace(self, trace_id, limit):
        return ()


class EvidenceServiceTests(unittest.TestCase):
    def test_client_failure_searches_caller_and_affected_dependency(self) -> None:
        repository = FakeRepository()
        jaeger = FakeJaeger()
        service = EvidenceService(repository, jaeger, FakeOpenSearch(), 5, 10)  # type: ignore[arg-type]
        job = InvestigationJob(
            "JOB-1",
            "INC-1",
            "running",
            START,
            START,
            START + timedelta(minutes=5),
            START,
        )
        trigger = IncidentTrigger(
            "fingerprint",
            "DependencyErrors",
            "error_ratio",
            "threshold",
            "critical",
            START,
            START,
            1.0,
            1.0,
            True,
            0,
            labels={
                "service_name": "checkout",
                "sentinel_dependency_name": "payment",
                "span_name": "charge",
            },
        )
        incident = Incident(
            "INC-1", "payment", "open", "critical", START, START, None, (trigger,)
        )

        service.collect_initial(job, incident, 300, 60)

        self.assertIn(("checkout", "charge"), jaeger.searches)
        self.assertIn(("payment", None), jaeger.searches)


if __name__ == "__main__":
    unittest.main()
