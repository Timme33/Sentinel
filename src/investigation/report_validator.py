from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from sentinel.domain.errors import InvestigationError
from sentinel.domain.investigation import EvidenceItem


def parse_and_validate_report(
    response: str, evidence: Sequence[EvidenceItem]
) -> Mapping[str, Any]:
    try:
        report = json.loads(_strip_fence(response))
    except json.JSONDecodeError as error:
        raise InvestigationError("Investigator did not return valid JSON") from error
    if not isinstance(report, Mapping):
        raise InvestigationError("Investigation report must be a JSON object")
    required = ("summary", "incident_assessment", "primary_hypothesis", "observations", "unknowns")
    missing = [field for field in required if field not in report]
    if missing:
        raise InvestigationError(f"Investigation report is missing: {', '.join(missing)}")
    known_ids = {item.id for item in evidence}
    cited = set(_evidence_ids(report))
    unknown_ids = cited - known_ids
    if unknown_ids:
        raise InvestigationError(
            f"Investigation report cites unknown evidence: {', '.join(sorted(unknown_ids))}"
        )
    hypothesis = report.get("primary_hypothesis")
    if not isinstance(hypothesis, Mapping) or not hypothesis.get("evidence_ids"):
        raise InvestigationError("Primary hypothesis must cite evidence")
    observations = report.get("observations")
    if not isinstance(observations, list):
        raise InvestigationError("observations must be a list")
    for observation in observations:
        if not isinstance(observation, Mapping) or not observation.get("evidence_ids"):
            raise InvestigationError("Every observation must cite evidence")
    return report


def _evidence_ids(value: Any):
    if isinstance(value, Mapping):
        for key, child in value.items():
            if key == "evidence_ids" and isinstance(child, list):
                yield from (str(item) for item in child)
            else:
                yield from _evidence_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from _evidence_ids(child)


def _strip_fence(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("```json") and stripped.endswith("```"):
        return stripped[7:-3].strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        return stripped[3:-3].strip()
    return stripped
