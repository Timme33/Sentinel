from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sentinel.domain.models import MetricSeries, RedObservation
from sentinel.telemetry.prometheus import PrometheusAdapter
from sentinel.telemetry.red_queries import RedQueries


class RedMetricsReader:
    def __init__(self, prometheus: PrometheusAdapter, queries: RedQueries) -> None:
        self.prometheus = prometheus
        self.queries = queries

    def snapshot(
        self, at: Optional[datetime] = None
    ) -> tuple[RedObservation, ...]:
        query_time = at or datetime.now(timezone.utc)
        request_rates = _latest_by_operation(
            self.prometheus.query(self.queries.request_rate, query_time)
        )
        error_ratios = _latest_by_operation(
            self.prometheus.query(self.queries.error_ratio, query_time)
        )
        latencies = _latest_by_operation(
            self.prometheus.query(self.queries.p95_latency_ms, query_time)
        )

        operations = sorted(request_rates.keys() | error_ratios.keys() | latencies.keys())
        return tuple(
            RedObservation(
                timestamp=query_time,
                service=service,
                operation=operation,
                request_rate=request_rates.get((service, operation)),
                error_ratio=error_ratios.get((service, operation)),
                p95_latency_ms=latencies.get((service, operation)),
            )
            for service, operation in operations
        )


def _latest_by_operation(
    series: tuple[MetricSeries, ...],
) -> dict[tuple[str, str], float]:
    values: dict[tuple[str, str], float] = {}
    for item in series:
        service = item.labels.get("service_name")
        operation = item.labels.get("span_name")
        if service is not None and operation is not None and item.points:
            values[(service, operation)] = item.points[-1].value
    return values
