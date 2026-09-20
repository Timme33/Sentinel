from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Optional
from uuid import uuid4

from sentinel.domain.errors import IncidentStoreError
from sentinel.domain.models import DetectionSnapshot, Incident, IncidentTrigger


@dataclass(frozen=True)
class ReconcileResult:
    opened: tuple[str, ...] = ()
    updated: tuple[str, ...] = ()
    resolved: tuple[str, ...] = ()


class SQLiteIncidentStore:
    """Durable service-level incident grouping and trigger state."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self._connect() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS incidents (
                        id TEXT PRIMARY KEY,
                        service TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
                        severity TEXT NOT NULL,
                        opened_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        resolved_at TEXT
                    );

                    CREATE UNIQUE INDEX IF NOT EXISTS one_open_incident_per_service
                    ON incidents(service)
                    WHERE status = 'open';

                    CREATE TABLE IF NOT EXISTS incident_triggers (
                        incident_id TEXT NOT NULL,
                        fingerprint TEXT NOT NULL,
                        alert_name TEXT NOT NULL,
                        signal TEXT NOT NULL,
                        method TEXT NOT NULL,
                        severity TEXT NOT NULL,
                        first_seen_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL,
                        initial_value REAL,
                        latest_value REAL,
                        active INTEGER NOT NULL CHECK (active IN (0, 1)),
                        missing_polls INTEGER NOT NULL DEFAULT 0,
                        labels_json TEXT NOT NULL,
                        annotations_json TEXT NOT NULL,
                        PRIMARY KEY (incident_id, fingerprint),
                        FOREIGN KEY (incident_id) REFERENCES incidents(id)
                    );
                    """
                )
        except sqlite3.Error as error:
            raise IncidentStoreError(f"Could not initialize {self.path}: {error}") from error

    def reconcile(
        self,
        snapshot: DetectionSnapshot,
        missing_polls_to_resolve: int,
    ) -> ReconcileResult:
        if missing_polls_to_resolve <= 0:
            raise ValueError("missing_polls_to_resolve must be positive")

        opened: list[str] = []
        updated: set[str] = set()
        resolved: list[str] = []
        active_fingerprints = {signal.fingerprint for signal in snapshot.signals}

        try:
            with self._connect() as connection:
                open_by_service = self._open_incident_ids(connection)

                for signal in snapshot.signals:
                    incident_id = open_by_service.get(signal.service)
                    if incident_id is None:
                        incident_id = _incident_id()
                        connection.execute(
                            """
                            INSERT INTO incidents (
                                id, service, status, severity, opened_at, last_seen_at
                            ) VALUES (?, ?, 'open', ?, ?, ?)
                            """,
                            (
                                incident_id,
                                signal.service,
                                signal.severity,
                                _iso(snapshot.observed_at),
                                _iso(snapshot.observed_at),
                            ),
                        )
                        open_by_service[signal.service] = incident_id
                        opened.append(incident_id)
                    else:
                        updated.add(incident_id)

                    self._upsert_trigger(connection, incident_id, signal)
                    connection.execute(
                        """
                        UPDATE incidents
                        SET last_seen_at = ?, severity = ?
                        WHERE id = ?
                        """,
                        (
                            _iso(snapshot.observed_at),
                            self._incident_severity(connection, incident_id),
                            incident_id,
                        ),
                    )

                active_rows = connection.execute(
                    """
                    SELECT incident_id, fingerprint, missing_polls
                    FROM incident_triggers
                    WHERE active = 1
                    """
                ).fetchall()
                for row in active_rows:
                    if row["fingerprint"] in active_fingerprints:
                        continue
                    missing_polls = row["missing_polls"] + 1
                    still_active = missing_polls < missing_polls_to_resolve
                    connection.execute(
                        """
                        UPDATE incident_triggers
                        SET missing_polls = ?, active = ?
                        WHERE incident_id = ? AND fingerprint = ?
                        """,
                        (
                            missing_polls,
                            1 if still_active else 0,
                            row["incident_id"],
                            row["fingerprint"],
                        ),
                    )
                    updated.add(row["incident_id"])

                for incident_id in self._incidents_without_active_triggers(connection):
                    connection.execute(
                        """
                        UPDATE incidents
                        SET status = 'resolved', resolved_at = ?
                        WHERE id = ? AND status = 'open'
                        """,
                        (_iso(snapshot.observed_at), incident_id),
                    )
                    resolved.append(incident_id)
                    updated.discard(incident_id)
        except sqlite3.Error as error:
            raise IncidentStoreError(f"Could not update incidents: {error}") from error

        return ReconcileResult(
            opened=tuple(opened),
            updated=tuple(sorted(updated - set(opened))),
            resolved=tuple(resolved),
        )

    def list_incidents(
        self, status: Optional[str] = None, limit: int = 100
    ) -> tuple[Incident, ...]:
        if status not in (None, "open", "resolved"):
            raise ValueError("status must be 'open', 'resolved', or omitted")
        if limit <= 0:
            raise ValueError("limit must be positive")
        query = "SELECT * FROM incidents"
        parameters: list[object] = []
        if status is not None:
            query += " WHERE status = ?"
            parameters.append(status)
        query += " ORDER BY opened_at DESC LIMIT ?"
        parameters.append(limit)
        try:
            with self._connect() as connection:
                rows = connection.execute(query, parameters).fetchall()
                return tuple(self._incident_from_row(connection, row) for row in rows)
        except sqlite3.Error as error:
            raise IncidentStoreError(f"Could not list incidents: {error}") from error

    def get_incident(self, incident_id: str) -> Optional[Incident]:
        try:
            with self._connect() as connection:
                row = connection.execute(
                    "SELECT * FROM incidents WHERE id = ?", (incident_id,)
                ).fetchone()
                if row is None:
                    return None
                return self._incident_from_row(connection, row)
        except sqlite3.Error as error:
            raise IncidentStoreError(f"Could not read incident: {error}") from error

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @staticmethod
    def _open_incident_ids(connection: sqlite3.Connection) -> dict[str, str]:
        rows = connection.execute(
            "SELECT id, service FROM incidents WHERE status = 'open'"
        ).fetchall()
        return {row["service"]: row["id"] for row in rows}

    @staticmethod
    def _upsert_trigger(connection, incident_id, signal) -> None:
        connection.execute(
            """
            INSERT INTO incident_triggers (
                incident_id, fingerprint, alert_name, signal, method, severity,
                first_seen_at, last_seen_at, initial_value, latest_value,
                active, missing_polls, labels_json, annotations_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 0, ?, ?)
            ON CONFLICT(incident_id, fingerprint) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                latest_value = excluded.latest_value,
                severity = excluded.severity,
                active = 1,
                missing_polls = 0,
                labels_json = excluded.labels_json,
                annotations_json = excluded.annotations_json
            """,
            (
                incident_id,
                signal.fingerprint,
                signal.alert_name,
                signal.signal,
                signal.method,
                signal.severity,
                _iso(signal.first_active_at),
                _iso(signal.observed_at),
                signal.value,
                signal.value,
                json.dumps(dict(signal.labels), sort_keys=True),
                json.dumps(dict(signal.annotations), sort_keys=True),
            ),
        )

    @staticmethod
    def _incident_severity(connection, incident_id: str) -> str:
        severities = [
            row["severity"]
            for row in connection.execute(
                "SELECT severity FROM incident_triggers WHERE incident_id = ?",
                (incident_id,),
            ).fetchall()
        ]
        return max(severities, key=_severity_rank, default="warning")

    @staticmethod
    def _incidents_without_active_triggers(connection) -> tuple[str, ...]:
        rows = connection.execute(
            """
            SELECT incidents.id
            FROM incidents
            WHERE incidents.status = 'open'
              AND NOT EXISTS (
                  SELECT 1 FROM incident_triggers
                  WHERE incident_triggers.incident_id = incidents.id
                    AND incident_triggers.active = 1
              )
            """
        ).fetchall()
        return tuple(row["id"] for row in rows)

    @staticmethod
    def _incident_from_row(connection, row) -> Incident:
        trigger_rows = connection.execute(
            """
            SELECT * FROM incident_triggers
            WHERE incident_id = ?
            ORDER BY first_seen_at, signal
            """,
            (row["id"],),
        ).fetchall()
        triggers = tuple(
            IncidentTrigger(
                fingerprint=trigger["fingerprint"],
                alert_name=trigger["alert_name"],
                signal=trigger["signal"],
                method=trigger["method"],
                severity=trigger["severity"],
                first_seen_at=_datetime(trigger["first_seen_at"]),
                last_seen_at=_datetime(trigger["last_seen_at"]),
                initial_value=trigger["initial_value"],
                latest_value=trigger["latest_value"],
                active=bool(trigger["active"]),
                missing_polls=trigger["missing_polls"],
            )
            for trigger in trigger_rows
        )
        return Incident(
            id=row["id"],
            service=row["service"],
            status=row["status"],
            severity=row["severity"],
            opened_at=_datetime(row["opened_at"]),
            last_seen_at=_datetime(row["last_seen_at"]),
            resolved_at=_optional_datetime(row["resolved_at"]),
            triggers=triggers,
        )


def _incident_id() -> str:
    return f"INC-{uuid4().hex[:12].upper()}"


def _severity_rank(severity: str) -> int:
    return {"info": 0, "warning": 1, "critical": 2}.get(severity, 1)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Incident timestamps must include a timezone")
    return value.astimezone(timezone.utc).isoformat()


def _datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _optional_datetime(value: Optional[str]) -> Optional[datetime]:
    return None if value is None else _datetime(value)
