from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any, Callable, Mapping, Optional, Sequence
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sentinel.domain.errors import TelemetryError
from sentinel.domain.investigation import LogRecord


JsonTransport = Callable[[str, Mapping[str, Any], float], Mapping[str, Any]]


class OpenSearchAdapter:
    """Bounded, read-only log searches against OpenSearch."""

    def __init__(
        self,
        base_url: str,
        index_pattern: str = "otel-logs-*",
        timeout_seconds: float = 10.0,
        transport: Optional[JsonTransport] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.index_pattern = index_pattern
        self.timeout_seconds = timeout_seconds
        self._transport = transport or self._http_post_json

    def logs_for_trace(self, trace_id: str, limit: int = 50) -> tuple[LogRecord, ...]:
        return self._search([{"term": {"traceId": trace_id}}], limit)

    def search_logs(
        self,
        start: datetime,
        end: datetime,
        services: Sequence[str] = (),
        severities: Sequence[str] = (),
        keywords: Sequence[str] = (),
        limit: int = 50,
    ) -> tuple[LogRecord, ...]:
        filters: list[Mapping[str, Any]] = [
            {"range": {"@timestamp": {"gte": _iso(start), "lte": _iso(end)}}}
        ]
        if services:
            filters.append({"terms": {"resource.service.name": list(services)}})
        if severities:
            filters.append({"terms": {"severity": list(severities)}})
        if keywords:
            filters.append(
                {
                    "query_string": {
                        "query": " OR ".join(_escape_keyword(word) for word in keywords),
                        "fields": ["body"],
                    }
                }
            )
        return self._search(filters, limit)

    def _search(
        self, filters: Sequence[Mapping[str, Any]], limit: int
    ) -> tuple[LogRecord, ...]:
        if limit <= 0 or limit > 500:
            raise ValueError("log limit must be between 1 and 500")
        body = {
            "size": limit,
            "sort": [{"@timestamp": {"order": "asc"}}],
            "query": {"bool": {"filter": list(filters)}},
        }
        payload = self._transport(
            f"{self.base_url}/{self.index_pattern}/_search", body, self.timeout_seconds
        )
        return _parse_logs(payload)

    @staticmethod
    def _http_post_json(
        url: str, body: Mapping[str, Any], timeout_seconds: float
    ) -> Mapping[str, Any]:
        request = Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                payload = json.load(response)
        except HTTPError as error:
            raise TelemetryError(f"OpenSearch returned HTTP {error.code}") from error
        except URLError as error:
            raise TelemetryError(f"Could not reach OpenSearch: {error.reason}") from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise TelemetryError("OpenSearch returned invalid JSON") from error
        return _mapping(payload, "OpenSearch response")


def _parse_logs(payload: Mapping[str, Any]) -> tuple[LogRecord, ...]:
    hits = _mapping(payload.get("hits"), "OpenSearch hits").get("hits", [])
    if not isinstance(hits, list):
        raise TelemetryError("OpenSearch hit list was invalid")
    records = []
    for hit in hits:
        source = _mapping(_mapping(hit, "OpenSearch hit").get("_source"), "log source")
        resource = source.get("resource", {})
        resource_map = resource if isinstance(resource, Mapping) else {}
        service = resource_map.get("service.name")
        if service is None and isinstance(resource_map.get("service"), Mapping):
            service = resource_map["service"].get("name")
        records.append(
            LogRecord(
                timestamp=_datetime(source.get("@timestamp")),
                service=str(service or "unknown"),
                severity=str(source.get("severity", source.get("severityText", "unknown"))),
                body=str(source.get("body", "")),
                trace_id=_optional_string(source.get("traceId")),
                span_id=_optional_string(source.get("spanId")),
                attributes=source.get("attributes", {})
                if isinstance(source.get("attributes"), Mapping)
                else {},
            )
        )
    return tuple(records)


def _datetime(value: Any) -> datetime:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise TelemetryError(f"Invalid log timestamp: {value!r}") from error


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("OpenSearch timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat()


def _optional_string(value: Any) -> Optional[str]:
    return None if value in (None, "") else str(value)


def _escape_keyword(value: str) -> str:
    return '"' + value.replace('"', '\\"') + '"'


def _mapping(value: Any, description: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TelemetryError(f"{description} was not an object")
    return value
