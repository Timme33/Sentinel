from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional


@dataclass(frozen=True)
class InvestigationJob:
    id: str
    incident_id: str
    status: str
    created_at: datetime
    not_before: datetime
    deadline: datetime
    trigger_cutoff_at: datetime
    attempts: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    model: Optional[str] = None
    thread_id: Optional[str] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    job_id: str
    kind: str
    source: str
    summary: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class TraceSummary:
    trace_id: str
    started_at: datetime
    duration_ms: float
    services: tuple[str, ...]
    error_count: int
    root_operation: str
    spans: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class LogRecord:
    timestamp: datetime
    service: str
    severity: str
    body: str
    trace_id: Optional[str]
    span_id: Optional[str]
    attributes: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentResult:
    response: str
    thread_id: Optional[str] = None
