from __future__ import annotations

from datetime import datetime, timezone
import logging
import time
from typing import Protocol

from sentinel.domain.investigation import AgentResult, InvestigationJob
from sentinel.incidents.store import SQLiteIncidentStore
from sentinel.investigation.context_builder import InvestigationContextBuilder
from sentinel.investigation.evidence_service import EvidenceService
from sentinel.investigation.report_validator import parse_and_validate_report
from sentinel.investigation.repository import SQLiteInvestigationRepository


LOGGER = logging.getLogger(__name__)


class InvestigationAgent(Protocol):
    model: str

    def investigate(self, job: InvestigationJob, context: str) -> AgentResult:
        ...


class InvestigationWorker:
    def __init__(
        self,
        repository: SQLiteInvestigationRepository,
        incidents: SQLiteIncidentStore,
        evidence: EvidenceService,
        context_builder: InvestigationContextBuilder,
        agent: InvestigationAgent,
        lookback_seconds: int,
        lookahead_seconds: int,
        poll_interval_seconds: int,
    ) -> None:
        self.repository = repository
        self.incidents = incidents
        self.evidence = evidence
        self.context_builder = context_builder
        self.agent = agent
        self.lookback_seconds = lookback_seconds
        self.lookahead_seconds = lookahead_seconds
        self.poll_interval_seconds = poll_interval_seconds

    def run_once(self, job_id: str | None = None) -> InvestigationJob | None:
        now = datetime.now(timezone.utc)
        job = self.repository.claim(job_id, now) if job_id else self.repository.claim_due(now)
        if job is None:
            return None
        try:
            incident = self.incidents.get_incident(job.incident_id)
            if incident is None:
                raise ValueError(f"Incident {job.incident_id} no longer exists")
            initial = self.evidence.collect_initial(
                job,
                incident,
                self.lookback_seconds,
                self.lookahead_seconds,
            )
            context = self.context_builder.build(job, incident, initial)
            result = self.agent.investigate(job, context)
            all_evidence = self.repository.list_evidence(job.id)
            report = parse_and_validate_report(result.response, all_evidence)
            self.repository.complete(
                job.id,
                report,
                datetime.now(timezone.utc),
                self.agent.model,
                result.thread_id,
            )
        except Exception as error:
            LOGGER.exception("Investigation %s failed", job.id)
            self.repository.fail(job.id, str(error), datetime.now(timezone.utc))
        return self.repository.get(job.id)

    def run_forever(self) -> None:
        while True:
            completed = self.run_once()
            if completed is None:
                time.sleep(self.poll_interval_seconds)
