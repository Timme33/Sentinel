from __future__ import annotations

from datetime import datetime, timezone
import hashlib

from sentinel.domain.models import (
    DetectionSignal,
    DetectionSnapshot,
    PrometheusAlert,
)
from sentinel.telemetry.prometheus import PrometheusAdapter


class PrometheusAlertSource:
    """Converts active Sentinel-labeled Prometheus alerts into signals."""

    def __init__(self, prometheus: PrometheusAdapter) -> None:
        self.prometheus = prometheus

    def poll(self) -> DetectionSnapshot:
        observed_at = datetime.now(timezone.utc)
        signals = tuple(
            _to_signal(alert, observed_at)
            for alert in self.prometheus.firing_alerts()
            if alert.labels.get("sentinel") == "true"
        )
        return DetectionSnapshot(observed_at=observed_at, signals=signals)


def _to_signal(alert: PrometheusAlert, observed_at: datetime) -> DetectionSignal:
    scope = alert.labels.get("sentinel_scope", "server_operation")
    caller = alert.labels.get("service_name", "unknown")
    dependency = alert.labels.get("sentinel_dependency_name")
    service = dependency if scope == "client_dependency_operation" and dependency else caller
    operation = alert.labels.get("span_name", "unknown")
    signal = alert.labels.get("sentinel_signal", "unknown")
    method = alert.labels.get("sentinel_method", "prometheus_alert")
    fingerprint_source = "\x00".join(
        (alert.name, scope, caller, dependency or "", operation, signal, method)
    )
    fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8")).hexdigest()[:20]
    return DetectionSignal(
        fingerprint=fingerprint,
        observed_at=observed_at,
        first_active_at=alert.active_at or observed_at,
        service=service,
        signal=signal,
        method=method,
        alert_name=alert.name,
        severity=alert.labels.get("severity", "warning"),
        value=alert.value,
        labels=alert.labels,
        annotations=alert.annotations,
    )
