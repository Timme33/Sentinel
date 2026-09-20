from __future__ import annotations

from datetime import datetime, timezone
import unittest

from sentinel.detection.prometheus_alerts import PrometheusAlertSource
from sentinel.domain.models import PrometheusAlert


class FakePrometheus:
    def firing_alerts(self):
        return (
            PrometheusAlert(
                name="SentinelExtremeErrorRatio",
                state="firing",
                active_at=datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc),
                value=0.74,
                labels={
                    "sentinel": "true",
                    "service_name": "payment",
                    "sentinel_signal": "error_ratio",
                    "sentinel_method": "extreme_threshold",
                    "severity": "critical",
                },
            ),
            PrometheusAlert(
                name="UnrelatedAlert",
                state="firing",
                active_at=None,
                value=1.0,
                labels={"service_name": "payment"},
            ),
        )


class PrometheusAlertSourceTests(unittest.TestCase):
    def test_filters_and_converts_sentinel_alerts(self) -> None:
        snapshot = PrometheusAlertSource(FakePrometheus()).poll()  # type: ignore[arg-type]
        self.assertEqual(len(snapshot.signals), 1)
        signal = snapshot.signals[0]
        self.assertEqual(signal.service, "payment")
        self.assertEqual(signal.signal, "error_ratio")
        self.assertEqual(signal.method, "extreme_threshold")
        self.assertEqual(signal.severity, "critical")


if __name__ == "__main__":
    unittest.main()

