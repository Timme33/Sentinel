from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest

from sentinel.domain.models import DetectionSignal, DetectionSnapshot
from sentinel.incidents.store import SQLiteIncidentStore


START = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


def signal(
    fingerprint: str,
    service: str,
    name: str,
    observed_at: datetime,
    severity: str = "warning",
) -> DetectionSignal:
    return DetectionSignal(
        fingerprint=fingerprint,
        observed_at=observed_at,
        first_active_at=observed_at,
        service=service,
        signal=name,
        method="test",
        alert_name=f"Test{name}",
        severity=severity,
        value=1.0,
    )


class IncidentStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.store = SQLiteIncidentStore(
            Path(self.temp_directory.name) / "sentinel.db"
        )
        self.store.initialize()

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    def test_groups_multiple_signals_for_one_service(self) -> None:
        result = self.store.reconcile(
            DetectionSnapshot(
                observed_at=START,
                signals=(
                    signal("error", "payment", "error_ratio", START, "critical"),
                    signal("latency", "payment", "p95_latency_ms", START),
                ),
            ),
            missing_polls_to_resolve=2,
        )

        self.assertEqual(len(result.opened), 1)
        incidents = self.store.list_incidents(status="open")
        self.assertEqual(len(incidents), 1)
        self.assertEqual(incidents[0].service, "payment")
        self.assertEqual(incidents[0].severity, "critical")
        self.assertEqual(len(incidents[0].triggers), 2)

    def test_keeps_different_services_separate(self) -> None:
        result = self.store.reconcile(
            DetectionSnapshot(
                observed_at=START,
                signals=(
                    signal("payment-error", "payment", "error_ratio", START),
                    signal("checkout-error", "checkout", "error_ratio", START),
                ),
            ),
            missing_polls_to_resolve=2,
        )
        self.assertEqual(len(result.opened), 2)

    def test_resolves_after_two_successful_missing_polls(self) -> None:
        opened = self.store.reconcile(
            DetectionSnapshot(
                observed_at=START,
                signals=(signal("error", "payment", "error_ratio", START),),
            ),
            missing_polls_to_resolve=2,
        )
        incident_id = opened.opened[0]

        first_missing = self.store.reconcile(
            DetectionSnapshot(observed_at=START + timedelta(minutes=1), signals=()),
            missing_polls_to_resolve=2,
        )
        self.assertEqual(first_missing.resolved, ())
        self.assertEqual(self.store.get_incident(incident_id).status, "open")  # type: ignore[union-attr]

        second_missing = self.store.reconcile(
            DetectionSnapshot(observed_at=START + timedelta(minutes=2), signals=()),
            missing_polls_to_resolve=2,
        )
        self.assertEqual(second_missing.resolved, (incident_id,))
        self.assertEqual(self.store.get_incident(incident_id).status, "resolved")  # type: ignore[union-attr]

    def test_new_occurrence_after_resolution_opens_new_incident(self) -> None:
        first = self.store.reconcile(
            DetectionSnapshot(
                observed_at=START,
                signals=(signal("error", "payment", "error_ratio", START),),
            ),
            missing_polls_to_resolve=1,
        )
        self.store.reconcile(
            DetectionSnapshot(observed_at=START + timedelta(minutes=1), signals=()),
            missing_polls_to_resolve=1,
        )
        second = self.store.reconcile(
            DetectionSnapshot(
                observed_at=START + timedelta(minutes=2),
                signals=(
                    signal(
                        "error",
                        "payment",
                        "error_ratio",
                        START + timedelta(minutes=2),
                    ),
                ),
            ),
            missing_polls_to_resolve=1,
        )
        self.assertNotEqual(first.opened[0], second.opened[0])


if __name__ == "__main__":
    unittest.main()

