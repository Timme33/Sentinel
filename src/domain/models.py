from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Optional


@dataclass(frozen=True)
class MetricPoint:
    timestamp: datetime
    value: float


@dataclass(frozen=True)
class MetricSeries:
    labels: Mapping[str, str]
    points: tuple[MetricPoint, ...]


@dataclass(frozen=True)
class RedObservation:
    timestamp: datetime
    service: str
    operation: str
    request_rate: Optional[float]
    error_ratio: Optional[float]
    p95_latency_ms: Optional[float]


@dataclass(frozen=True)
class PrometheusAlert:
    name: str
    state: str
    active_at: Optional[datetime]
    value: Optional[float]
    labels: Mapping[str, str] = field(default_factory=dict)
    annotations: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DetectionSignal:
    fingerprint: str
    observed_at: datetime
    first_active_at: datetime
    service: str
    signal: str
    method: str
    alert_name: str
    severity: str
    value: Optional[float]
    labels: Mapping[str, str] = field(default_factory=dict)
    annotations: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DetectionSnapshot:
    observed_at: datetime
    signals: tuple[DetectionSignal, ...]


@dataclass(frozen=True)
class IncidentTrigger:
    fingerprint: str
    alert_name: str
    signal: str
    method: str
    severity: str
    first_seen_at: datetime
    last_seen_at: datetime
    initial_value: Optional[float]
    latest_value: Optional[float]
    active: bool
    missing_polls: int
    labels: Mapping[str, str] = field(default_factory=dict)
    annotations: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Incident:
    id: str
    service: str
    status: str
    severity: str
    opened_at: datetime
    last_seen_at: datetime
    resolved_at: Optional[datetime]
    triggers: tuple[IncidentTrigger, ...] = ()
