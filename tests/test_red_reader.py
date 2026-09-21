from __future__ import annotations

from datetime import datetime, timezone
import unittest

from sentinel.domain.models import MetricPoint, MetricSeries
from sentinel.telemetry.red_queries import RedQueries
from sentinel.telemetry.red_reader import RedMetricsReader


NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


class FakePrometheus:
    def query(self, expression, at):
        values = {
            "request": (1.0, 2.0),
            "error": (0.0, 1.0),
            "latency": (5.0, 10.0),
        }[expression]
        return tuple(
            MetricSeries(
                labels={"service_name": "cart", "span_name": operation},
                points=(MetricPoint(timestamp=NOW, value=value),),
            )
            for operation, value in zip(("AddItem", "EmptyCart"), values)
        )


class RedMetricsReaderTests(unittest.TestCase):
    def test_keeps_operations_separate(self) -> None:
        reader = RedMetricsReader(
            FakePrometheus(),  # type: ignore[arg-type]
            RedQueries("request", "error", "latency"),
        )

        observations = reader.snapshot(NOW)

        self.assertEqual(len(observations), 2)
        self.assertEqual(observations[0].operation, "AddItem")
        self.assertEqual(observations[1].operation, "EmptyCart")
        self.assertEqual(observations[1].error_ratio, 1.0)


if __name__ == "__main__":
    unittest.main()
