from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Callable, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from sentinel.domain.errors import TelemetryError
from sentinel.domain.investigation import TraceSummary


JsonTransport = Callable[[str, float], Mapping[str, Any]]


class JaegerAdapter:
    """Bounded, read-only access to the Jaeger query API."""

    def __init__(
        self,
        base_url: str,
        timeout_seconds: float = 10.0,
        transport: Optional[JsonTransport] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._transport = transport or self._http_get_json

    def search_traces(
        self,
        service: str,
        start: datetime,
        end: datetime,
        operation: Optional[str] = None,
        limit: int = 10,
    ) -> tuple[TraceSummary, ...]:
        if end <= start:
            raise ValueError("end must be later than start")
        if limit <= 0:
            raise ValueError("limit must be positive")
        parameters = {
            "service": service,
            "start": str(_microseconds(start)),
            "end": str(_microseconds(end)),
            "limit": str(limit),
        }
        if operation and operation != "unknown":
            parameters["operation"] = operation
        payload = self._get("/traces", parameters)
        return _parse_traces(payload)[:limit]

    def get_trace(self, trace_id: str) -> Optional[TraceSummary]:
        payload = self._get(f"/traces/{trace_id}", {})
        traces = _parse_traces(payload)
        return traces[0] if traces else None

    def _get(self, path: str, parameters: Mapping[str, str]) -> Mapping[str, Any]:
        url = f"{self.base_url}{path}"
        encoded = urlencode(parameters)
        if encoded:
            url = f"{url}?{encoded}"
        return self._transport(url, self.timeout_seconds)

    @staticmethod
    def _http_get_json(url: str, timeout_seconds: float) -> Mapping[str, Any]:
        request = Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                payload = json.load(response)
        except HTTPError as error:
            raise TelemetryError(f"Jaeger returned HTTP {error.code}") from error
        except URLError as error:
            raise TelemetryError(f"Could not reach Jaeger: {error.reason}") from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise TelemetryError("Jaeger returned invalid JSON") from error
        return _mapping(payload, "Jaeger response")


def _parse_traces(payload: Mapping[str, Any]) -> tuple[TraceSummary, ...]:
    raw_traces = payload.get("data", [])
    if not isinstance(raw_traces, list):
        raise TelemetryError("Jaeger trace data was not a list")
    traces = []
    for raw_trace in raw_traces:
        trace = _mapping(raw_trace, "Jaeger trace")
        spans = trace.get("spans", [])
        processes = trace.get("processes", {})
        if not isinstance(spans, list) or not isinstance(processes, Mapping):
            raise TelemetryError("Jaeger trace has invalid spans or processes")
        normalized = tuple(_span(span, processes) for span in spans)
        if not normalized:
            continue
        started_us = min(int(span["start_time_us"]) for span in normalized)
        ended_us = max(
            int(span["start_time_us"]) + int(span["duration_us"])
            for span in normalized
        )
        roots = [span for span in normalized if not span["parent_span_id"]]
        root = roots[0] if roots else normalized[0]
        traces.append(
            TraceSummary(
                trace_id=str(trace.get("traceID", root["trace_id"])),
                started_at=datetime.fromtimestamp(started_us / 1_000_000, timezone.utc),
                duration_ms=(ended_us - started_us) / 1000,
                services=tuple(sorted({str(span["service"]) for span in normalized})),
                error_count=sum(bool(span["error"]) for span in normalized),
                root_operation=str(root["operation"]),
                spans=normalized,
            )
        )
    return tuple(sorted(traces, key=lambda item: item.started_at, reverse=True))


def _span(raw: Any, processes: Mapping[str, Any]) -> Mapping[str, Any]:
    span = _mapping(raw, "Jaeger span")
    process = processes.get(str(span.get("processID")), {})
    process_map = process if isinstance(process, Mapping) else {}
    tags = _tags(span.get("tags"))
    references = span.get("references", [])
    parent = ""
    if isinstance(references, list):
        for reference in references:
            if isinstance(reference, Mapping) and reference.get("refType") == "CHILD_OF":
                parent = str(reference.get("spanID", ""))
                break
    error = tags.get("error") is True or str(tags.get("otel.status_code", "")).upper() == "ERROR"
    return {
        "trace_id": str(span.get("traceID", "")),
        "span_id": str(span.get("spanID", "")),
        "parent_span_id": parent,
        "service": str(process_map.get("serviceName", "unknown")),
        "operation": str(span.get("operationName", "unknown")),
        "start_time_us": int(span.get("startTime", 0)),
        "duration_us": int(span.get("duration", 0)),
        "error": error,
        "tags": tags,
    }


def _tags(raw: Any) -> Mapping[str, Any]:
    if not isinstance(raw, list):
        return {}
    return {
        str(item.get("key")): item.get("value")
        for item in raw
        if isinstance(item, Mapping) and item.get("key") is not None
    }


def _microseconds(value: datetime) -> int:
    if value.tzinfo is None:
        raise ValueError("Jaeger timestamps must include a timezone")
    return int(value.timestamp() * 1_000_000)


def _mapping(value: Any, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TelemetryError(f"{description} was not an object")
    return value
