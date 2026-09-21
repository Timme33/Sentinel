from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping


@dataclass(frozen=True)
class PrometheusConfig:
    base_url: str
    timeout_seconds: float


@dataclass(frozen=True)
class DetectionConfig:
    poll_interval_seconds: int
    missing_polls_to_resolve: int
    lookback: str
    services: tuple[str, ...]


@dataclass(frozen=True)
class IncidentConfig:
    database_path: Path


@dataclass(frozen=True)
class JaegerConfig:
    base_url: str
    timeout_seconds: float


@dataclass(frozen=True)
class OpenSearchConfig:
    base_url: str
    index_pattern: str
    timeout_seconds: float


@dataclass(frozen=True)
class InvestigationConfig:
    quiet_period_seconds: int
    max_wait_seconds: int
    worker_poll_interval_seconds: int
    lookback_seconds: int
    lookahead_seconds: int
    trace_limit: int
    log_limit: int


@dataclass(frozen=True)
class CodexConfig:
    model: str
    reasoning_effort: str


@dataclass(frozen=True)
class SentinelConfig:
    prometheus: PrometheusConfig
    detection: DetectionConfig
    incidents: IncidentConfig
    jaeger: JaegerConfig
    opensearch: OpenSearchConfig
    investigation: InvestigationConfig
    codex: CodexConfig


def load_config(path: str | Path) -> SentinelConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as config_file:
        raw = json.load(config_file)

    root = _mapping(raw, "configuration")
    prometheus = _mapping(root.get("prometheus"), "prometheus configuration")
    detection = _mapping(root.get("detection"), "detection configuration")
    incidents = _mapping(root.get("incidents"), "incident configuration")
    jaeger = _mapping(root.get("jaeger", {}), "Jaeger configuration")
    opensearch = _mapping(root.get("opensearch", {}), "OpenSearch configuration")
    investigation = _mapping(root.get("investigation", {}), "investigation configuration")
    codex = _mapping(root.get("codex", {}), "Codex configuration")

    services_value = detection.get("services")
    if not isinstance(services_value, list) or not services_value:
        raise ValueError("detection.services must be a non-empty list")
    services = tuple(str(service) for service in services_value)

    database_path = Path(str(incidents["database_path"]))
    if not database_path.is_absolute():
        database_path = (config_path.parent.parent / database_path).resolve()

    config = SentinelConfig(
        prometheus=PrometheusConfig(
            base_url=str(prometheus["base_url"]).rstrip("/"),
            timeout_seconds=float(prometheus.get("timeout_seconds", 10.0)),
        ),
        detection=DetectionConfig(
            poll_interval_seconds=int(detection.get("poll_interval_seconds", 60)),
            missing_polls_to_resolve=int(
                detection.get("missing_polls_to_resolve", 2)
            ),
            lookback=str(detection.get("lookback", "5m")),
            services=services,
        ),
        incidents=IncidentConfig(database_path=database_path),
        jaeger=JaegerConfig(
            base_url=str(jaeger.get("base_url", "http://localhost:16686/api")).rstrip("/"),
            timeout_seconds=float(jaeger.get("timeout_seconds", 10.0)),
        ),
        opensearch=OpenSearchConfig(
            base_url=str(opensearch.get("base_url", "http://localhost:9200")).rstrip("/"),
            index_pattern=str(opensearch.get("index_pattern", "otel-logs-*")),
            timeout_seconds=float(opensearch.get("timeout_seconds", 10.0)),
        ),
        investigation=InvestigationConfig(
            quiet_period_seconds=int(investigation.get("quiet_period_seconds", 120)),
            max_wait_seconds=int(investigation.get("max_wait_seconds", 300)),
            worker_poll_interval_seconds=int(
                investigation.get("worker_poll_interval_seconds", 5)
            ),
            lookback_seconds=int(investigation.get("lookback_seconds", 300)),
            lookahead_seconds=int(investigation.get("lookahead_seconds", 60)),
            trace_limit=int(investigation.get("trace_limit", 8)),
            log_limit=int(investigation.get("log_limit", 30)),
        ),
        codex=CodexConfig(
            model=str(codex.get("model", "gpt-5.6-sol")),
            reasoning_effort=str(codex.get("reasoning_effort", "high")),
        ),
    )
    _validate(config)
    return config


def _validate(config: SentinelConfig) -> None:
    if not config.prometheus.base_url.startswith(("http://", "https://")):
        raise ValueError("prometheus.base_url must begin with http:// or https://")
    if config.prometheus.timeout_seconds <= 0:
        raise ValueError("prometheus.timeout_seconds must be positive")
    if config.detection.poll_interval_seconds <= 0:
        raise ValueError("detection.poll_interval_seconds must be positive")
    if config.detection.missing_polls_to_resolve <= 0:
        raise ValueError("detection.missing_polls_to_resolve must be positive")
    for name, value in (
        ("investigation.quiet_period_seconds", config.investigation.quiet_period_seconds),
        ("investigation.max_wait_seconds", config.investigation.max_wait_seconds),
        ("investigation.worker_poll_interval_seconds", config.investigation.worker_poll_interval_seconds),
        ("investigation.lookback_seconds", config.investigation.lookback_seconds),
        ("investigation.lookahead_seconds", config.investigation.lookahead_seconds),
        ("investigation.trace_limit", config.investigation.trace_limit),
        ("investigation.log_limit", config.investigation.log_limit),
    ):
        if value <= 0:
            raise ValueError(f"{name} must be positive")


def _mapping(value: Any, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{description} must be a JSON object")
    return value
