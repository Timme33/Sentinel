from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from sentinel.domain.models import DetectionSignal, DetectionSnapshot
from sentinel.incidents.store import SQLiteIncidentStore
from sentinel.investigation.repository import SQLiteInvestigationRepository


START = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


def signal(fingerprint: str, observed_at: datetime) -> DetectionSignal:
    return DetectionSignal(
        fingerprint=fingerprint,
        observed_at=observed_at,
        first_active_at=observed_at,
        service="payment",
        signal="error_ratio",
        method="test",
        alert_name="PaymentErrors",
        severity="critical",
        value=1.0,
        labels={"service_name": "checkout", "span_name": "charge"},
    )


class InvestigationRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary_directory.name) / "sentinel.db"
        self.incidents = SQLiteIncidentStore(self.path)
        self.incidents.initialize()
        self.repository = SQLiteInvestigationRepository(self.path)
        self.repository.initialize()

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_new_trigger_postpones_job_but_value_update_does_not(self) -> None:
        first = self.incidents.reconcile(
            DetectionSnapshot(START, (signal("one", START),)), 2
        )
        job_id = self.repository.observe_new_triggers(
            first.new_trigger_incidents, START, 120, 300
        )[0]
        initial = self.repository.get(job_id)
        self.assertEqual(initial.not_before, START + timedelta(seconds=120))  # type: ignore[union-attr]

        repeated = self.incidents.reconcile(
            DetectionSnapshot(
                START + timedelta(seconds=60),
                (signal("one", START + timedelta(seconds=60)),),
            ),
            2,
        )
        self.assertEqual(repeated.new_trigger_incidents, ())
        self.assertEqual(self.repository.get(job_id).not_before, initial.not_before)  # type: ignore[union-attr]

        second = self.incidents.reconcile(
            DetectionSnapshot(
                START + timedelta(seconds=100),
                (
                    signal("one", START + timedelta(seconds=100)),
                    signal("two", START + timedelta(seconds=100)),
                ),
            ),
            2,
        )
        self.repository.observe_new_triggers(
            second.new_trigger_incidents, START + timedelta(seconds=100), 120, 300
        )
        postponed = self.repository.get(job_id)
        self.assertEqual(postponed.not_before, START + timedelta(seconds=220))  # type: ignore[union-attr]
        self.assertEqual(postponed.deadline, START + timedelta(seconds=300))  # type: ignore[union-attr]

    def test_claim_due_and_persist_report(self) -> None:
        result = self.incidents.reconcile(
            DetectionSnapshot(START, (signal("one", START),)), 2
        )
        job_id = self.repository.observe_new_triggers(
            result.new_trigger_incidents, START, 10, 30
        )[0]
        self.assertIsNone(self.repository.claim_due(START + timedelta(seconds=9)))
        claimed = self.repository.claim_due(START + timedelta(seconds=10))
        self.assertEqual(claimed.id, job_id)  # type: ignore[union-attr]
        evidence = self.repository.add_evidence(
            job_id, "metric", "test", "error ratio was 1", {"value": 1}, START
        )
        self.assertEqual(evidence.id, "M1")
        report = {"summary": "payment failed"}
        self.repository.complete(job_id, report, START, "test-model", "thread-1")
        self.assertEqual(self.repository.report(job_id), report)
        self.assertEqual(self.repository.get(job_id).status, "completed")  # type: ignore[union-attr]


if __name__ == "__main__":
    unittest.main()
