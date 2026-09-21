from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from sentinel.config import load_config
from sentinel.investigation.evidence_service import EvidenceService, evidence_payload
from sentinel.investigation.repository import SQLiteInvestigationRepository
from sentinel.telemetry.jaeger import JaegerAdapter
from sentinel.telemetry.opensearch import OpenSearchAdapter


def create_server(service: EvidenceService, job_id: str):
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as error:
        raise RuntimeError("FastMCP is not installed") from error

    mcp = FastMCP("Sentinel telemetry", json_response=True)

    @mcp.tool()
    def list_evidence() -> list[Mapping[str, Any]]:
        """List all evidence already retrieved for this investigation."""
        return evidence_payload(service.repository.list_evidence(job_id))

    @mcp.tool()
    def search_traces(
        service_name: str,
        start: str,
        end: str,
        operation: str | None = None,
        limit: int = 5,
    ) -> list[Mapping[str, Any]]:
        """Search a bounded time window for traces from one service and operation."""
        return evidence_payload(
            service.search_traces(
                job_id,
                service_name,
                _datetime(start),
                _datetime(end),
                operation,
                limit,
            )
        )

    @mcp.tool()
    def get_trace(trace_id: str) -> list[Mapping[str, Any]]:
        """Get a complete trace plus any logs correlated by trace ID."""
        return evidence_payload(service.get_trace(job_id, trace_id))

    @mcp.tool()
    def search_logs(
        start: str,
        end: str,
        services: Sequence[str] = (),
        severities: Sequence[str] = (),
        keywords: Sequence[str] = (),
        limit: int = 20,
    ) -> list[Mapping[str, Any]]:
        """Search bounded logs by time, service, severity, and optional keywords."""
        return evidence_payload(
            service.search_logs(
                job_id,
                _datetime(start),
                _datetime(end),
                services,
                severities,
                keywords,
                limit,
            )
        )

    return mcp


def main() -> None:
    config_path = Path(os.environ["SENTINEL_CONFIG"])
    job_id = os.environ["SENTINEL_JOB_ID"]
    config = load_config(config_path)
    repository = SQLiteInvestigationRepository(config.incidents.database_path)
    repository.initialize()
    service = EvidenceService(
        repository,
        JaegerAdapter(config.jaeger.base_url, config.jaeger.timeout_seconds),
        OpenSearchAdapter(
            config.opensearch.base_url,
            config.opensearch.index_pattern,
            config.opensearch.timeout_seconds,
        ),
        config.investigation.trace_limit,
        config.investigation.log_limit,
    )
    create_server(service, job_id).run(transport="stdio")


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


if __name__ == "__main__":
    main()
