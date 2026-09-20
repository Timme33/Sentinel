from __future__ import annotations

from datetime import datetime, timezone
import unittest

from sentinel.telemetry.prometheus import PrometheusAdapter


class PrometheusAdapterTests(unittest.TestCase):
    def test_parses_vector_query(self) -> None:
        def transport(url: str, timeout: float):
            self.assertIn("/api/v1/query?", url)
            return {
                "status": "success",
                "data": {
                    "resultType": "vector",
                    "result": [
                        {
                            "metric": {"service_name": "payment"},
                            "value": [1_700_000_000, "0.74"],
                        }
                    ],
                },
            }

        result = PrometheusAdapter(
            "http://prometheus:9090", transport=transport
        ).query("example")
        self.assertEqual(result[0].labels["service_name"], "payment")
        self.assertEqual(result[0].points[0].value, 0.74)

    def test_parses_range_query(self) -> None:
        def transport(url: str, timeout: float):
            self.assertIn("/api/v1/query_range?", url)
            return {
                "status": "success",
                "data": {
                    "resultType": "matrix",
                    "result": [
                        {
                            "metric": {"service_name": "checkout"},
                            "values": [
                                [1_700_000_000, "1.0"],
                                [1_700_000_060, "2.0"],
                            ],
                        }
                    ],
                },
            }

        adapter = PrometheusAdapter("http://prometheus:9090", transport=transport)
        result = adapter.query_range(
            "example",
            datetime(2026, 1, 1, tzinfo=timezone.utc),
            datetime(2026, 1, 1, 0, 2, tzinfo=timezone.utc),
        )
        self.assertEqual([point.value for point in result[0].points], [1.0, 2.0])

    def test_returns_only_firing_alerts(self) -> None:
        def transport(url: str, timeout: float):
            return {
                "status": "success",
                "data": {
                    "alerts": [
                        {
                            "labels": {
                                "alertname": "SentinelExtremeErrorRatio",
                                "service_name": "payment",
                            },
                            "annotations": {"summary": "payment errors"},
                            "state": "firing",
                            "activeAt": "2026-09-19T12:00:00Z",
                            "value": "7.4e-01",
                        },
                        {
                            "labels": {"alertname": "StillPending"},
                            "annotations": {},
                            "state": "pending",
                            "activeAt": "2026-09-19T12:00:00Z",
                            "value": "1",
                        },
                    ]
                },
            }

        alerts = PrometheusAdapter(
            "http://prometheus:9090", transport=transport
        ).firing_alerts()
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].name, "SentinelExtremeErrorRatio")
        self.assertEqual(alerts[0].value, 0.74)


if __name__ == "__main__":
    unittest.main()

