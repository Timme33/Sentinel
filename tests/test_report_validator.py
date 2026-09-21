from __future__ import annotations

import json
import unittest

from sentinel.domain.errors import InvestigationError
from sentinel.domain.investigation import EvidenceItem
from sentinel.investigation.report_validator import parse_and_validate_report


def report(evidence_id: str):
    return {
        "summary": "Payment calls failed",
        "incident_assessment": "confirmed",
        "primary_hypothesis": {
            "statement": "payment was unreachable",
            "confidence": 0.9,
            "reasoning": "trace failed",
            "evidence_ids": [evidence_id],
        },
        "alternative_hypotheses": [],
        "observations": [
            {"statement": "trace has an error", "evidence_ids": [evidence_id]}
        ],
        "unknowns": [],
        "recommended_next_steps": [],
    }


class ReportValidatorTests(unittest.TestCase):
    def test_accepts_known_evidence(self) -> None:
        evidence = (EvidenceItem("T1", "job", "trace", "jaeger", "trace"),)
        parsed = parse_and_validate_report(json.dumps(report("T1")), evidence)
        self.assertEqual(parsed["incident_assessment"], "confirmed")

    def test_rejects_unknown_evidence(self) -> None:
        with self.assertRaises(InvestigationError):
            parse_and_validate_report(json.dumps(report("T99")), ())


if __name__ == "__main__":
    unittest.main()
