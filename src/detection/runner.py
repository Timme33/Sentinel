from __future__ import annotations

import logging
import time

from sentinel.detection.base import DetectionSource
from sentinel.domain.errors import PrometheusError
from sentinel.incidents.store import ReconcileResult, SQLiteIncidentStore


LOGGER = logging.getLogger(__name__)


class DetectionRunner:
    def __init__(
        self,
        source: DetectionSource,
        store: SQLiteIncidentStore,
        poll_interval_seconds: int,
        missing_polls_to_resolve: int,
    ) -> None:
        self.source = source
        self.store = store
        self.poll_interval_seconds = poll_interval_seconds
        self.missing_polls_to_resolve = missing_polls_to_resolve

    def run_once(self) -> ReconcileResult:
        snapshot = self.source.poll()
        return self.store.reconcile(snapshot, self.missing_polls_to_resolve)

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
