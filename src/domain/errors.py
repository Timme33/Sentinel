class SentinelError(RuntimeError):
    """Base exception for expected Sentinel runtime failures."""


class PrometheusError(SentinelError):
    """Prometheus was unreachable or returned an invalid response."""


class IncidentStoreError(SentinelError):
    """Incident state could not be read or written."""


class InvestigationError(SentinelError):
    """An investigation could not be scheduled, executed, or persisted."""


class TelemetryError(SentinelError):
    """A telemetry backend was unreachable or returned invalid data."""
