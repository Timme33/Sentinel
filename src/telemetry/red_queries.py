from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class RedQueries:
    request_rate: str
    error_ratio: str
    p95_latency_ms: str


def build_red_queries(services: tuple[str, ...], lookback: str = "5m") -> RedQueries:
    if not services:
        raise ValueError("At least one service is required")
    if not re.fullmatch(r"[1-9][0-9]*(ms|s|m|h|d|w|y)", lookback):
        raise ValueError(f"Unsupported PromQL lookback: {lookback!r}")

    service_pattern = "|".join(_escape_promql_regex(service) for service in services)
    base_matchers = (
        f'span_kind="SPAN_KIND_SERVER",'
        f'service_name=~"^({service_pattern})$"'
    )
    total_rate = (
        "sum by (service_name) ("
        f"rate(traces_span_metrics_calls_total{{{base_matchers}}}[{lookback}])"
        ")"
    )
    error_rate = (
        "sum by (service_name) ("
        "rate(traces_span_metrics_calls_total{"
        f'{base_matchers},status_code="STATUS_CODE_ERROR"'
        f"}}[{lookback}])"
        ")"
    )
    error_ratio = (
        f"(({error_rate}) / ({total_rate})) "
        f"or on (service_name) (0 * ({total_rate}))"
    )
    p95_latency = (
        "histogram_quantile(0.95, "
        "sum by (le, service_name) ("
        "rate(traces_span_metrics_duration_milliseconds_bucket{"
        f"{base_matchers}"
        f"}}[{lookback}])"
        ")"
        ")"
    )
    return RedQueries(
        request_rate=total_rate,
        error_ratio=error_ratio,
        p95_latency_ms=p95_latency,
    )


def _escape_promql_regex(value: str) -> str:
    # Escape regex metacharacters while leaving hyphens valid in PromQL strings.
    return re.sub(r"([.^$*+?{}\[\]\\|()])", r"\\\1", value)
