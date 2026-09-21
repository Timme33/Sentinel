from __future__ import annotations

import logging
import time

from sentinel.detection.base import DetectionSource
from sentinel.domain.errors import PrometheusError
from sentinel.incidents.store import ReconcileResult, SQLiteIncidentStore
from sentinel.investigation.repository import SQLiteInvestigationRepository


LOGGER = logging.getLogger(__name__)


class DetectionRunner:
    def __init__(
        self,
        source: DetectionSource,
        store: SQLiteIncidentStore,
        poll_interval_seconds: int,
        missing_polls_to_resolve: int,
        investigations: SQLiteInvestigationRepository | None = None,
        investigation_quiet_period_seconds: int = 120,
        investigation_max_wait_seconds: int = 300,
    ) -> None:
        self.source = source
        self.store = store
        self.poll_interval_seconds = poll_interval_seconds
        self.missing_polls_to_resolve = missing_polls_to_resolve
        self.investigations = investigations
        self.investigation_quiet_period_seconds = investigation_quiet_period_seconds
        self.investigation_max_wait_seconds = investigation_max_wait_seconds

    def run_once(self) -> ReconcileResult:
        snapshot = self.source.poll()
        result = self.store.reconcile(snapshot, self.missing_polls_to_resolve)
        if self.investigations is not None and result.new_trigger_incidents:
            self.investigations.observe_new_triggers(
                result.new_trigger_incidents,
                snapshot.observed_at,
                self.investigation_quiet_period_seconds,
                self.investigation_max_wait_seconds,
            )
        return result

    def run_forever(self) -> None:
        while True:
            try:
                result = self.run_once()
                _log_result(result)
            except PrometheusError as error:
                LOGGER.error("Detection poll failed; incident state was not changed: %s", error)
            time.sleep(self.poll_interval_seconds)


def _log_result(result: ReconcileResult) -> None:
    for incident_id in result.opened:
        LOGGER.info("Opened incident %s", incident_id)
    for incident_id in result.resolved:
        LOGGER.info("Resolved incident %s", incident_id)
