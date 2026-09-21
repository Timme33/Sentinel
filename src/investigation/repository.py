from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Mapping, Optional
from uuid import uuid4

from sentinel.domain.errors import InvestigationError
from sentinel.domain.investigation import EvidenceItem, InvestigationJob


class SQLiteInvestigationRepository:
    """Durable investigation queue, evidence ledger, and report store."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS investigation_jobs (
                        id TEXT PRIMARY KEY,
                        incident_id TEXT NOT NULL UNIQUE,
                        status TEXT NOT NULL CHECK (
                            status IN ('pending', 'running', 'completed', 'failed')
                        ),
                        created_at TEXT NOT NULL,
                        not_before TEXT NOT NULL,
                        deadline TEXT NOT NULL,
                        trigger_cutoff_at TEXT NOT NULL,
                        attempts INTEGER NOT NULL DEFAULT 0,
                        started_at TEXT,
                        completed_at TEXT,
                        model TEXT,
                        thread_id TEXT,
                        error TEXT,
                        FOREIGN KEY (incident_id) REFERENCES incidents(id)
                    );

                    CREATE INDEX IF NOT EXISTS investigation_jobs_due
                    ON investigation_jobs(status, not_before);

                    CREATE TABLE IF NOT EXISTS investigation_evidence (
                        job_id TEXT NOT NULL,
                        id TEXT NOT NULL,
                        kind TEXT NOT NULL,
                        source TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY (job_id, id),
                        FOREIGN KEY (job_id) REFERENCES investigation_jobs(id)
                    );

                    CREATE TABLE IF NOT EXISTS investigation_reports (
                        job_id TEXT PRIMARY KEY,
                        report_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (job_id) REFERENCES investigation_jobs(id)
                    );
                    """
                )
        except sqlite3.Error as error:
            raise InvestigationError(
                f"Could not initialize investigation storage: {error}"
            ) from error

    def observe_new_triggers(
        self,
        incident_ids: tuple[str, ...],
        observed_at: datetime,
        quiet_period_seconds: int,
        max_wait_seconds: int,
    ) -> tuple[str, ...]:
        """Create or postpone pending jobs when an incident gains new evidence."""
        if quiet_period_seconds <= 0 or max_wait_seconds <= 0:
            raise ValueError("investigation wait periods must be positive")
        changed: list[str] = []
        try:
            with self._connect() as connection:
                for incident_id in dict.fromkeys(incident_ids):
                    row = connection.execute(
                        "SELECT * FROM investigation_jobs WHERE incident_id = ?",
                        (incident_id,),
                    ).fetchone()
                    proposed = observed_at + timedelta(seconds=quiet_period_seconds)
                    if row is None:
                        job_id = f"JOB-{uuid4().hex[:12].upper()}"
                        deadline = observed_at + timedelta(seconds=max_wait_seconds)
                        connection.execute(
                            """
                            INSERT INTO investigation_jobs (
                                id, incident_id, status, created_at, not_before,
                                deadline, trigger_cutoff_at
                            ) VALUES (?, ?, 'pending', ?, ?, ?, ?)
                            """,
                            (
                                job_id,
                                incident_id,
                                _iso(observed_at),
                                _iso(min(proposed, deadline)),
                                _iso(deadline),
                                _iso(observed_at),
                            ),
                        )
                        changed.append(job_id)
                    elif row["status"] == "pending":
                        not_before = min(proposed, _datetime(row["deadline"]))
                        connection.execute(
                            """
                            UPDATE investigation_jobs
                            SET not_before = ?, trigger_cutoff_at = ?
                            WHERE id = ?
                            """,
                            (_iso(not_before), _iso(observed_at), row["id"]),
                        )
                        changed.append(row["id"])
        except sqlite3.Error as error:
            raise InvestigationError(f"Could not schedule investigation: {error}") from error
        return tuple(changed)

    def claim_due(self, now: datetime) -> Optional[InvestigationJob]:
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    """
                    SELECT * FROM investigation_jobs
                    WHERE status = 'pending' AND not_before <= ?
                    ORDER BY not_before, created_at LIMIT 1
                    """,
                    (_iso(now),),
                ).fetchone()
                if row is None:
                    return None
                updated = connection.execute(
                    """
                    UPDATE investigation_jobs
                    SET status = 'running', started_at = ?, attempts = attempts + 1,
                        error = NULL
                    WHERE id = ? AND status = 'pending'
                    """,
                    (_iso(now), row["id"]),
                ).rowcount
                if updated != 1:
                    return None
                row = connection.execute(
                    "SELECT * FROM investigation_jobs WHERE id = ?", (row["id"],)
                ).fetchone()
                return _job(row)
        except sqlite3.Error as error:
            raise InvestigationError(f"Could not claim investigation: {error}") from error

    def claim(self, job_id: str, now: datetime) -> Optional[InvestigationJob]:
        try:
            with self._connect() as connection:
                updated = connection.execute(
                    """
                    UPDATE investigation_jobs
                    SET status = 'running', started_at = ?, attempts = attempts + 1,
                        error = NULL
                    WHERE id = ? AND status IN ('pending', 'failed')
                    """,
                    (_iso(now), job_id),
                ).rowcount
                if updated != 1:
                    return None
                row = connection.execute(
                    "SELECT * FROM investigation_jobs WHERE id = ?", (job_id,)
                ).fetchone()
                return _job(row)
        except sqlite3.Error as error:
            raise InvestigationError(f"Could not claim investigation: {error}") from error

    def add_evidence(
        self,
        job_id: str,
        kind: str,
        source: str,
        summary: str,
        payload: Mapping[str, Any],
        created_at: datetime,
    ) -> EvidenceItem:
        prefix = {"detection": "D", "metric": "M", "trace": "T", "log": "L"}.get(
            kind, "E"
        )
        try:
            with self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                count = connection.execute(
                    "SELECT COUNT(*) FROM investigation_evidence WHERE job_id = ? AND kind = ?",
                    (job_id, kind),
                ).fetchone()[0]
                evidence_id = f"{prefix}{count + 1}"
                connection.execute(
                    """
                    INSERT INTO investigation_evidence (
                        job_id, id, kind, source, summary, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        evidence_id,
                        kind,
                        source,
                        summary,
                        json.dumps(payload, sort_keys=True, default=str),
                        _iso(created_at),
                    ),
                )
            return EvidenceItem(
                id=evidence_id,
                job_id=job_id,
                kind=kind,
                source=source,
                summary=summary,
                payload=dict(payload),
                created_at=created_at,
            )
        except sqlite3.Error as error:
            raise InvestigationError(f"Could not save evidence: {error}") from error

    def list_evidence(self, job_id: str) -> tuple[EvidenceItem, ...]:
        try:
            with self._connect() as connection:
                rows = connection.execute(
                    """
                    SELECT * FROM investigation_evidence
                    WHERE job_id = ? ORDER BY created_at, id
                    """,
                    (job_id,),
                ).fetchall()
            return tuple(_evidence(row) for row in rows)
        except sqlite3.Error as error:
            raise InvestigationError(f"Could not read evidence: {error}") from error

    def complete(
        self,
        job_id: str,
        report: Mapping[str, Any],
        completed_at: datetime,
        model: Optional[str],
        thread_id: Optional[str],
    ) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO investigation_reports (job_id, report_json, created_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(job_id) DO UPDATE SET
                        report_json = excluded.report_json,
                        created_at = excluded.created_at
                    """,
                    (job_id, json.dumps(report, sort_keys=True), _iso(completed_at)),
                )
                connection.execute(
                    """
                    UPDATE investigation_jobs
                    SET status = 'completed', completed_at = ?, model = ?,
                        thread_id = ?, error = NULL
                    WHERE id = ?
                    """,
                    (_iso(completed_at), model, thread_id, job_id),
                )
        except sqlite3.Error as error:
            raise InvestigationError(f"Could not complete investigation: {error}") from error

    def fail(self, job_id: str, error: str, completed_at: datetime) -> None:
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    UPDATE investigation_jobs
                    SET status = 'failed', completed_at = ?, error = ? WHERE id = ?
                    """,
                    (_iso(completed_at), error[:2000], job_id),
                )
        except sqlite3.Error as database_error:
            raise InvestigationError(
                f"Could not record investigation failure: {database_error}"
            ) from database_error

    def get(self, job_id: str) -> Optional[InvestigationJob]:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM investigation_jobs WHERE id = ?", (job_id,)
                ).fetchone()
                return None if row is None else _job(row)
        except sqlite3.Error as error:
            raise InvestigationError(f"Could not read investigation: {error}") from error

    def list(self, status: Optional[str] = None, limit: int = 100) -> tuple[InvestigationJob, ...]:
        if status not in (None, "pending", "running", "completed", "failed"):
            raise ValueError("invalid investigation status")
        query = "SELECT * FROM investigation_jobs"
        parameters: list[object] = []
        if status:
            query += " WHERE status = ?"
            parameters.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        parameters.append(limit)
        try:
            with self._connect() as connection:
                return tuple(_job(row) for row in connection.execute(query, parameters))
        except sqlite3.Error as error:
            raise InvestigationError(f"Could not list investigations: {error}") from error

    def report(self, job_id: str) -> Optional[Mapping[str, Any]]:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT report_json FROM investigation_reports WHERE job_id = ?",
                    (job_id,),
                ).fetchone()
                return None if row is None else json.loads(row["report_json"])
        except sqlite3.Error as error:
            raise InvestigationError(f"Could not read report: {error}") from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection


def _job(row: sqlite3.Row) -> InvestigationJob:
    return InvestigationJob(
        id=row["id"], incident_id=row["incident_id"], status=row["status"],
        created_at=_datetime(row["created_at"]), not_before=_datetime(row["not_before"]),
        deadline=_datetime(row["deadline"]),
        trigger_cutoff_at=_datetime(row["trigger_cutoff_at"]), attempts=row["attempts"],
        started_at=_optional_datetime(row["started_at"]),
        completed_at=_optional_datetime(row["completed_at"]), model=row["model"],
        thread_id=row["thread_id"], error=row["error"],
    )


def _evidence(row: sqlite3.Row) -> EvidenceItem:
    return EvidenceItem(
        id=row["id"], job_id=row["job_id"], kind=row["kind"], source=row["source"],
        summary=row["summary"], payload=json.loads(row["payload_json"]),
        created_at=_datetime(row["created_at"]),
    )


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("investigation timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat()


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _optional_datetime(value: Optional[str]) -> Optional[datetime]:
    return None if value is None else _datetime(value)
