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
class SentinelConfig:
    prometheus: PrometheusConfig
    detection: DetectionConfig
    incidents: IncidentConfig


def load_config(path: str | Path) -> SentinelConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as config_file:
        raw = json.load(config_file)

    root = _mapping(raw, "configuration")
    prometheus = _mapping(root.get("prometheus"), "prometheus configuration")
    detection = _mapping(root.get("detection"), "detection configuration")
    incidents = _mapping(root.get("incidents"), "incident configuration")

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


def _mapping(value: Any, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{description} must be a JSON object")
    return value
