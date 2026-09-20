from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Callable, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sentinel.domain.errors import PrometheusError
from sentinel.domain.models import MetricPoint, MetricSeries, PrometheusAlert


JsonTransport = Callable[[str, float], Mapping[str, Any]]


class PrometheusAdapter:
    """Read-only adapter for the Prometheus HTTP API."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 10.0,
        transport: Optional[JsonTransport] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._transport = transport or self._http_get_json

    def ready(self) -> bool:
        request = Request(f"{self.base_url}/-/ready", method="GET")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return 200 <= response.status < 300
        except (HTTPError, URLError):
            return False

    def query(
        self, expression: str, at: Optional[datetime] = None
    ) -> tuple[MetricSeries, ...]:
        parameters = {"query": expression}
        if at is not None:
            parameters["time"] = _prometheus_time(at)
        payload = self._get("/api/v1/query", parameters)
        return _parse_query_result(payload)

    def query_range(
        self,
        expression: str,
        start: datetime,
        end: datetime,
        step_seconds: int = 60,
    ) -> tuple[MetricSeries, ...]:
        if end <= start:
            raise ValueError("end must be later than start")
        if step_seconds <= 0:
            raise ValueError("step_seconds must be positive")
        payload = self._get(
            "/api/v1/query_range",
            {
                "query": expression,
                "start": _prometheus_time(start),
                "end": _prometheus_time(end),
                "step": str(step_seconds),
            },
        )
        return _parse_query_result(payload)

    def firing_alerts(self) -> tuple[PrometheusAlert, ...]:
        payload = self._get("/api/v1/alerts", {})
        data = _mapping(payload.get("data"), "alert response data")
        raw_alerts = data.get("alerts", [])
        if not isinstance(raw_alerts, list):
            raise PrometheusError("Prometheus alerts field was not a list")

        alerts = []
        for raw_alert in raw_alerts:
            item = _mapping(raw_alert, "alert")
            if item.get("state") != "firing":
                continue
            labels = _string_mapping(item.get("labels"))
            alerts.append(
                PrometheusAlert(
                    name=labels.get("alertname", "unknown"),
                    state="firing",
                    active_at=_parse_rfc3339(item.get("activeAt")),
                    value=_optional_float(item.get("value")),
                    labels=labels,
                    annotations=_string_mapping(item.get("annotations")),
                )
            )
        return tuple(alerts)

    def _get(self, path: str, parameters: Mapping[str, str]) -> Mapping[str, Any]:
        encoded = urlencode(parameters)
        url = f"{self.base_url}{path}"
        if encoded:
            url = f"{url}?{encoded}"
        payload = self._transport(url, self.timeout_seconds)
        if payload.get("status") != "success":
            error_type = payload.get("errorType", "unknown")
            message = payload.get("error", "request failed")
            raise PrometheusError(f"Prometheus {error_type} error: {message}")
        return payload

    @staticmethod
    def _http_get_json(url: str, timeout_seconds: float) -> Mapping[str, Any]:
        request = Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                payload = json.load(response)
        except HTTPError as error:
            raise PrometheusError(
                f"Prometheus returned HTTP {error.code} for {url}"
            ) from error
        except URLError as error:
            raise PrometheusError(
                f"Could not reach Prometheus at {url}: {error.reason}"
            ) from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise PrometheusError("Prometheus returned invalid JSON") from error
        return _mapping(payload, "Prometheus response")


def _parse_query_result(payload: Mapping[str, Any]) -> tuple[MetricSeries, ...]:
    data = _mapping(payload.get("data"), "query response data")
    result_type = data.get("resultType")
    result = data.get("result")

    if result_type == "vector":
        if not isinstance(result, list):
            raise PrometheusError("Prometheus vector result was not a list")
        return tuple(_parse_vector_item(item) for item in result)
    if result_type == "matrix":
        if not isinstance(result, list):
            raise PrometheusError("Prometheus matrix result was not a list")
        return tuple(_parse_matrix_item(item) for item in result)
    if result_type == "scalar":
        timestamp, value = _parse_sample(result)
        return (MetricSeries(labels={}, points=(MetricPoint(timestamp, value),)),)
    raise PrometheusError(f"Unsupported Prometheus result type: {result_type!r}")


def _parse_vector_item(raw: Any) -> MetricSeries:
    item = _mapping(raw, "vector result")
    timestamp, value = _parse_sample(item.get("value"))
    return MetricSeries(
        labels=_string_mapping(item.get("metric")),
        points=(MetricPoint(timestamp=timestamp, value=value),),
    )


def _parse_matrix_item(raw: Any) -> MetricSeries:
    item = _mapping(raw, "matrix result")
    values = item.get("values")
    if not isinstance(values, list):
        raise PrometheusError("Prometheus matrix values field was not a list")
    return MetricSeries(
        labels=_string_mapping(item.get("metric")),
        points=tuple(
            MetricPoint(timestamp=timestamp, value=value)
            for timestamp, value in (_parse_sample(sample) for sample in values)
        ),
    )


def _parse_sample(raw: Any) -> tuple[datetime, float]:
    if not isinstance(raw, list) or len(raw) != 2:
        raise PrometheusError("Prometheus sample must contain timestamp and value")
    try:
        timestamp = datetime.fromtimestamp(float(raw[0]), tz=timezone.utc)
        value = float(raw[1])
    except (TypeError, ValueError) as error:
        raise PrometheusError(f"Invalid Prometheus sample: {raw!r}") from error
    return timestamp, value


def _prometheus_time(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Prometheus timestamps must include a timezone")
    return value.isoformat()


def _parse_rfc3339(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise PrometheusError(f"Invalid Prometheus alert time: {value!r}") from error


def _optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise PrometheusError(f"Invalid Prometheus alert value: {value!r}") from error


def _mapping(value: Any, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PrometheusError(f"{description} was not an object")
    return value


def _string_mapping(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    mapping = _mapping(value, "label set")
    return {str(key): str(item) for key, item in mapping.items()}
