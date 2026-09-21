from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from sentinel.domain.investigation import AgentResult
from sentinel.domain.models import DetectionSignal, DetectionSnapshot
from sentinel.incidents.store import SQLiteIncidentStore
from sentinel.investigation.context_builder import InvestigationContextBuilder
from sentinel.investigation.repository import SQLiteInvestigationRepository
from sentinel.investigation.worker import InvestigationWorker


START = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)


class FakeEvidenceService:
    def __init__(self, repository):
        self.repository = repository

    def collect_initial(self, job, incident, lookback_seconds, lookahead_seconds):
        self.repository.add_evidence(
            job.id, "metric", "test", "payment error ratio was 1", {"value": 1}, START
        )
        return self.repository.list_evidence(job.id)


class FakeAgent:
    model = "fake-model"

    def investigate(self, job, context):
        self.context = context
        report = {
            "summary": "Payment calls failed",
            "incident_assessment": "confirmed",
            "primary_hypothesis": {
                "statement": "payment failed",
                "confidence": 0.8,
                "reasoning": "metric alert",
                "evidence_ids": ["M1"],
            },
            "alternative_hypotheses": [],
            "observations": [
                {"statement": "error ratio was 1", "evidence_ids": ["M1"]}
            ],
            "unknowns": ["source-code cause"],
            "recommended_next_steps": ["inspect payment service"],
        }
        return AgentResult(json.dumps(report), "thread-1")


class InvestigationWorkerTests(unittest.TestCase):
    def test_runs_job_and_persists_validated_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sentinel.db"
            incidents = SQLiteIncidentStore(path)
            incidents.initialize()
            signal = DetectionSignal(
                "fingerprint",
                START,
                START,
                "payment",
                "error_ratio",
                "test",
                "PaymentErrors",
                "critical",
                1.0,
            )
            reconciled = incidents.reconcile(DetectionSnapshot(START, (signal,)), 2)
            repository = SQLiteInvestigationRepository(path)
            repository.initialize()
            job_id = repository.observe_new_triggers(
                reconciled.new_trigger_incidents, START, 120, 300
            )[0]
            agent = FakeAgent()
            worker = InvestigationWorker(
                repository,
                incidents,
                FakeEvidenceService(repository),  # type: ignore[arg-type]
                InvestigationContextBuilder(),
                agent,
                300,
                60,
                1,
            )

            result = worker.run_once(job_id)

            self.assertEqual(result.status, "completed")  # type: ignore[union-attr]
            self.assertEqual(repository.report(job_id)["incident_assessment"], "confirmed")  # type: ignore[index]
            self.assertIn("initial_evidence", agent.context)


if __name__ == "__main__":
    unittest.main()
