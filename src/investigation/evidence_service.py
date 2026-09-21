from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from sentinel.domain.investigation import EvidenceItem, InvestigationJob, LogRecord, TraceSummary
from sentinel.domain.models import Incident
from sentinel.investigation.repository import SQLiteInvestigationRepository
from sentinel.telemetry.jaeger import JaegerAdapter
from sentinel.telemetry.opensearch import OpenSearchAdapter


class EvidenceService:
    """Retrieves bounded telemetry and records exactly what the model can cite."""

    def __init__(
        self,
        repository: SQLiteInvestigationRepository,
        jaeger: JaegerAdapter,
        opensearch: OpenSearchAdapter,
        trace_limit: int,
        log_limit: int,
        max_query_window_seconds: int = 1800,
    ) -> None:
        self.repository = repository
        self.jaeger = jaeger
        self.opensearch = opensearch
        self.trace_limit = trace_limit
        self.log_limit = log_limit
        self.max_query_window_seconds = max_query_window_seconds

    def collect_initial(
        self,
        job: InvestigationJob,
        incident: Incident,
        lookback_seconds: int,
        lookahead_seconds: int,
    ) -> tuple[EvidenceItem, ...]:
        now = datetime.now(timezone.utc)
        cutoff_triggers = tuple(
            trigger
            for trigger in incident.triggers
            if trigger.first_seen_at <= job.trigger_cutoff_at
        )
        self.repository.add_evidence(
            job.id,
            "detection",
            "sentinel",
            f"Incident {incident.id} grouped {len(cutoff_triggers)} trigger(s) for {incident.service}",
            {
                "incident_id": incident.id,
                "service": incident.service,
                "severity": incident.severity,
                "opened_at": incident.opened_at.isoformat(),
                "trigger_cutoff_at": job.trigger_cutoff_at.isoformat(),
                "trigger_fingerprints": [trigger.fingerprint for trigger in cutoff_triggers],
            },
            now,
        )
        for trigger in cutoff_triggers:
            self.repository.add_evidence(
                job.id,
                "metric",
                "prometheus-alert",
                f"{trigger.alert_name}: {trigger.signal} on {_scope(trigger.labels)}",
                {
                    "alert_name": trigger.alert_name,
                    "signal": trigger.signal,
                    "method": trigger.method,
                    "severity": trigger.severity,
                    "initial_value": trigger.initial_value,
                    "latest_value": trigger.latest_value,
                    "first_seen_at": trigger.first_seen_at.isoformat(),
                    "last_seen_at": trigger.last_seen_at.isoformat(),
                    "labels": dict(trigger.labels),
                    "annotations": dict(trigger.annotations),
                },
                now,
            )

        start = incident.opened_at - timedelta(seconds=lookback_seconds)
        end = job.trigger_cutoff_at + timedelta(seconds=lookahead_seconds)
        trace_scopes = tuple(
            dict.fromkeys(
                (
                    trigger.labels.get("service_name", incident.service),
                    trigger.labels.get("span_name"),
                )
                for trigger in cutoff_triggers
            )
        ) or ((incident.service, None),)
        if all(service != incident.service for service, _ in trace_scopes):
            trace_scopes = (*trace_scopes, (incident.service, None))
        traces: dict[str, TraceSummary] = {}
        for service, operation in trace_scopes[:6]:
            for trace in self.jaeger.search_traces(
                service, start, end, operation, self.trace_limit
            ):
                traces[trace.trace_id] = trace
        ranked = sorted(
            traces.values(), key=lambda trace: (trace.error_count, trace.duration_ms), reverse=True
        )[: self.trace_limit]
        for trace in ranked:
            self._record_trace(job.id, trace)

        seen_logs: set[tuple[str, str, str]] = set()
        for trace in ranked:
            for record in self.opensearch.logs_for_trace(trace.trace_id, self.log_limit):
                key = (record.timestamp.isoformat(), record.service, record.body)
                if key not in seen_logs and len(seen_logs) < self.log_limit:
                    seen_logs.add(key)
                    self._record_log(job.id, record, "opensearch-trace-correlation")

        if len(seen_logs) < self.log_limit:
            services = tuple(
                sorted({incident.service, *(service for trace in ranked for service in trace.services)})
            )
            for record in self.opensearch.search_logs(
                start,
                end,
                services=services,
                severities=("WARN", "ERROR", "FATAL"),
                limit=self.log_limit - len(seen_logs),
            ):
                key = (record.timestamp.isoformat(), record.service, record.body)
                if key not in seen_logs:
                    seen_logs.add(key)
                    self._record_log(job.id, record, "opensearch-severity-window")
        return self.repository.list_evidence(job.id)

    def search_traces(
        self,
        job_id: str,
        service: str,
        start: datetime,
        end: datetime,
        operation: str | None = None,
        limit: int = 5,
    ) -> tuple[EvidenceItem, ...]:
        self._validate_window(start, end)
        before = {item.id for item in self.repository.list_evidence(job_id)}
        for trace in self.jaeger.search_traces(
            service, start, end, operation, min(limit, self.trace_limit)
        ):
            self._record_trace(job_id, trace)
        return tuple(
            item for item in self.repository.list_evidence(job_id) if item.id not in before
        )

    def get_trace(self, job_id: str, trace_id: str) -> tuple[EvidenceItem, ...]:
        trace = self.jaeger.get_trace(trace_id)
        if trace is None:
            return ()
        trace_item = self._record_trace(job_id, trace)
        log_items = tuple(
            self._record_log(job_id, record, "opensearch-trace-correlation")
            for record in self.opensearch.logs_for_trace(trace_id, self.log_limit)
        )
        return (trace_item, *log_items)

    def search_logs(
        self,
        job_id: str,
        start: datetime,
        end: datetime,
        services: Sequence[str] = (),
        severities: Sequence[str] = (),
        keywords: Sequence[str] = (),
        limit: int = 20,
    ) -> tuple[EvidenceItem, ...]:
        self._validate_window(start, end)
        return tuple(
            self._record_log(job_id, record, "opensearch-agent-query")
            for record in self.opensearch.search_logs(
                start,
                end,
                services=services,
                severities=severities,
                keywords=keywords,
                limit=min(limit, self.log_limit),
            )
        )

    def _record_trace(self, job_id: str, trace: TraceSummary) -> EvidenceItem:
        return self.repository.add_evidence(
            job_id,
            "trace",
            "jaeger",
            f"Trace {trace.trace_id}: {trace.root_operation}, {trace.duration_ms:.1f} ms, {trace.error_count} error span(s)",
            asdict(trace),
            datetime.now(timezone.utc),
        )

    def _record_log(self, job_id: str, record: LogRecord, source: str) -> EvidenceItem:
        return self.repository.add_evidence(
            job_id,
            "log",
            source,
            f"{record.timestamp.isoformat()} {record.service} {record.severity}: {record.body[:240]}",
            asdict(record),
            datetime.now(timezone.utc),
        )

    def _validate_window(self, start: datetime, end: datetime) -> None:
        if end <= start:
            raise ValueError("end must be later than start")
        if (end - start).total_seconds() > self.max_query_window_seconds:
            raise ValueError(
                f"query window cannot exceed {self.max_query_window_seconds} seconds"
            )


def evidence_payload(items: Sequence[EvidenceItem]) -> list[Mapping[str, Any]]:
    return [
        {
            "id": item.id,
            "kind": item.kind,
            "source": item.source,
            "summary": item.summary,
            "payload": item.payload,
        }
        for item in items
    ]


def _scope(labels: Mapping[str, str]) -> str:
    caller = labels.get("service_name", "unknown")
    operation = labels.get("span_name", "unknown")
    dependency = labels.get("sentinel_dependency_name")
    if dependency:
        return f"{caller} -> {dependency} ({operation})"
    return f"{caller} ({operation})"
