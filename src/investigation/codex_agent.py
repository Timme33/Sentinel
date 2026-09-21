from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Mapping

from sentinel.domain.errors import InvestigationError
from sentinel.domain.investigation import AgentResult, InvestigationJob


REPORT_SCHEMA: Mapping[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "summary",
        "incident_assessment",
        "primary_hypothesis",
        "alternative_hypotheses",
        "observations",
        "unknowns",
        "recommended_next_steps",
    ],
    "properties": {
        "summary": {"type": "string"},
        "incident_assessment": {
            "type": "string",
            "enum": ["confirmed", "possible", "benign", "insufficient_evidence"],
        },
        "primary_hypothesis": {"$ref": "#/$defs/hypothesis"},
        "alternative_hypotheses": {
            "type": "array",
            "items": {"$ref": "#/$defs/hypothesis"},
        },
        "observations": {
            "type": "array",
            "items": {"$ref": "#/$defs/cited_statement"},
        },
        "unknowns": {"type": "array", "items": {"type": "string"}},
        "recommended_next_steps": {"type": "array", "items": {"type": "string"}},
    },
    "$defs": {
        "hypothesis": {
            "type": "object",
            "additionalProperties": False,
            "required": ["statement", "confidence", "reasoning", "evidence_ids"],
            "properties": {
                "statement": {"type": "string"},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "reasoning": {"type": "string"},
                "evidence_ids": {"type": "array", "items": {"type": "string"}},
            },
        },
        "cited_statement": {
            "type": "object",
            "additionalProperties": False,
            "required": ["statement", "evidence_ids"],
            "properties": {
                "statement": {"type": "string"},
                "evidence_ids": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
}


class CodexInvestigationAgent:
    """Codex SDK adapter. Authentication is intentionally a deployment concern."""

    def __init__(
        self,
        config_path: Path,
        model: str,
        reasoning_effort: str,
        instructions: str,
    ) -> None:
        self.config_path = config_path
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.instructions = instructions

    def investigate(self, job: InvestigationJob, context: str) -> AgentResult:
        try:
            from openai_codex import Codex, Sandbox
        except ImportError as error:
            raise InvestigationError(
                "Codex SDK is not installed; install the investigation dependencies"
            ) from error
        mcp_config = {
            "mcp_servers": {
                "sentinel": {
                    "command": sys.executable,
                    "args": ["-m", "sentinel.investigation.mcp_server"],
                    "env": {
                        "SENTINEL_CONFIG": str(self.config_path),
                        "SENTINEL_JOB_ID": job.id,
                    },
                    "required": True,
                    "startup_timeout_sec": 20,
                    "tool_timeout_sec": 30,
                }
            },
            "model_reasoning_effort": self.reasoning_effort,
        }
        try:
            with Codex() as codex:
                thread = codex.thread_start(
                    model=self.model,
                    config=mcp_config,
                    developer_instructions=self.instructions,
                    sandbox=Sandbox.read_only,
                    ephemeral=True,
                )
                result = thread.run(
                    context,
                    effort=self.reasoning_effort,
                    output_schema=REPORT_SCHEMA,
                    sandbox=Sandbox.read_only,
                )
                if not result.final_response:
                    raise InvestigationError("Codex returned no final investigation report")
                return AgentResult(
                    response=result.final_response,
                    thread_id=getattr(thread, "id", None),
                )
        except InvestigationError:
            raise
        except Exception as error:
            raise InvestigationError(f"Codex investigation failed: {error}") from error
