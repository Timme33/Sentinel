# Sentinel

Sentinel is an automated incident-detection system for distributed applications.
This first pass monitors service-level RED signals in Prometheus, consumes firing
Prometheus alerts, groups concurrent signals by service, and persists incident
lifecycles in SQLite.

## Detection path

```text
OpenTelemetry Demo
        |
        v
Prometheus span metrics
        |
        v
RED recording rules
        |
        v
fixed and rolling PromQL alerts
        |
        v
Sentinel Prometheus alert source
        |
        v
service-level incident grouping
        |
        v
SQLite incident history
```

Prometheus performs the numerical detection. Sentinel owns alert consumption,
grouping, durable incident state, and the boundary that a future investigation
worker will consume.

## Local CLI

Sentinel requires Python 3.9 or newer and has no third-party runtime dependencies.

The `src/` directory is mapped directly to the `sentinel` Python package in
`pyproject.toml`. This keeps the repository layout flat while preserving normal
imports such as `from sentinel.telemetry import PrometheusAdapter`.

```bash
cp configs/local.example.json configs/local.json
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Available commands:

```bash
sentinel --config configs/local.json check
sentinel --config configs/local.json metrics snapshot
sentinel --config configs/local.json alerts list
sentinel --config configs/local.json detect --once
sentinel --config configs/local.json detect
sentinel --config configs/local.json incidents list
sentinel --config configs/local.json incidents show INC-XXXXXXXXXXXX
```

## Incident policy

- Only firing Prometheus alerts labeled `sentinel="true"` enter Sentinel.
- Multiple active signals for one service belong to one open incident.
- Different services remain separate incidents in this first pass.
- Repeated polls update existing triggers instead of creating duplicates.
- A trigger becomes inactive after two successful polls in which it is absent.
- An incident resolves after all of its triggers become inactive.
- Failed Prometheus requests never advance incident recovery.

## OpenTelemetry Demo

The integration files are stored in
[`integration/otel-demo`](integration/otel-demo/README.md). They leave the
adjacent upstream demo checkout unchanged while mounting Sentinel's Prometheus
configuration and rules and adding the Sentinel service through a Compose
override.

## Tests

```bash
python -m unittest discover -s tests -v
```
