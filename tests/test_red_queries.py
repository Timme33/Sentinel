from __future__ import annotations

import unittest

from sentinel.telemetry.red_queries import build_red_queries


class RedQueryTests(unittest.TestCase):
    def test_builds_server_red_queries(self) -> None:
        queries = build_red_queries(("payment", "frontend-proxy"), "5m")
        for expression in (
            queries.request_rate,
            queries.error_ratio,
            queries.p95_latency_ms,
        ):
            self.assertIn('span_kind="SPAN_KIND_SERVER"', expression)
            self.assertIn("payment|frontend-proxy", expression)
        self.assertIn('status_code="STATUS_CODE_ERROR"', queries.error_ratio)
        self.assertIn("histogram_quantile(0.95", queries.p95_latency_ms)
        self.assertIn("service_name, span_name", queries.request_rate)
        self.assertIn("le, service_name, span_name", queries.p95_latency_ms)

    def test_rejects_invalid_lookback(self) -> None:
        with self.assertRaises(ValueError):
            build_red_queries(("payment",), "five minutes")


if __name__ == "__main__":
    unittest.main()
