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
                    "sentinel_scope": "server_operation",
                    "service_name": "payment",
                    "span_name": "oteldemo.PaymentService/Charge",
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
        self.assertEqual(
            signal.labels["span_name"], "oteldemo.PaymentService/Charge"
        )

    def test_client_alert_is_grouped_around_dependency(self) -> None:
        class ClientFailurePrometheus:
            def firing_alerts(self):
                return (
                    PrometheusAlert(
                        name="SentinelClientDependencyExtremeErrorRatio",
                        state="firing",
                        active_at=None,
                        value=1.0,
                        labels={
                            "sentinel": "true",
                            "sentinel_scope": "client_dependency_operation",
                            "service_name": "checkout",
                            "sentinel_dependency_name": "payment",
                            "span_name": "oteldemo.PaymentService/Charge",
                            "sentinel_signal": "error_ratio",
                            "sentinel_method": "extreme_threshold",
                            "severity": "critical",
                        },
                    ),
                )

        snapshot = PrometheusAlertSource(ClientFailurePrometheus()).poll()  # type: ignore[arg-type]
        self.assertEqual(snapshot.signals[0].service, "payment")
        self.assertEqual(snapshot.signals[0].labels["service_name"], "checkout")

    def test_operations_have_distinct_fingerprints(self) -> None:
        base = {
            "sentinel": "true",
            "sentinel_scope": "server_operation",
            "service_name": "cart",
            "sentinel_signal": "error_ratio",
            "sentinel_method": "extreme_threshold",
            "severity": "critical",
        }

        class TwoOperationPrometheus:
            def firing_alerts(self):
                return tuple(
                    PrometheusAlert(
                        name="SentinelServerOperationExtremeErrorRatio",
                        state="firing",
                        active_at=None,
                        value=1.0,
                        labels={**base, "span_name": operation},
                    )
                    for operation in ("AddItem", "EmptyCart")
                )

        snapshot = PrometheusAlertSource(TwoOperationPrometheus()).poll()  # type: ignore[arg-type]
        self.assertNotEqual(
            snapshot.signals[0].fingerprint, snapshot.signals[1].fingerprint
        )


if __name__ == "__main__":
    unittest.main()
