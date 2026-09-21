from __future__ import annotations

from datetime import datetime, timezone
import unittest

from sentinel.telemetry.jaeger import JaegerAdapter


class JaegerAdapterTests(unittest.TestCase):
    def test_normalizes_trace_and_error_span(self) -> None:
        def transport(url, timeout):
            self.assertIn("service=payment", url)
            return {
                "data": [
                    {
                        "traceID": "abc",
                        "processes": {"p1": {"serviceName": "payment"}},
                        "spans": [
                            {
                                "traceID": "abc",
                                "spanID": "root",
                                "processID": "p1",
                                "operationName": "charge",
                                "startTime": 1_000_000,
                                "duration": 25_000,
                                "references": [],
                                "tags": [
                                    {"key": "otel.status_code", "value": "ERROR"}
                                ],
                            }
                        ],
                    }
                ]
            }

        adapter = JaegerAdapter("http://jaeger/api", transport=transport)
        traces = adapter.search_traces(
            "payment",
            datetime(1970, 1, 1, tzinfo=timezone.utc),
            datetime(1970, 1, 1, 0, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(traces[0].trace_id, "abc")
        self.assertEqual(traces[0].root_operation, "charge")
        self.assertEqual(traces[0].error_count, 1)
        self.assertEqual(traces[0].duration_ms, 25)


if __name__ == "__main__":
    unittest.main()
