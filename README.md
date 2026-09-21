# Sentinel

Sentinel is an AIOps system that detects failures in distributed applications and
automatically investigates them using metrics, traces, and logs.

Modern systems already produce large amounts of telemetry, but finding the
useful evidence during an incident still requires an engineer to move between
several tools and connect the signals manually. Existing automated investigation
products are often proprietary, tied to a specific observability platform, or
designed as much larger operational suites. There is still no clear, broadly
adopted open-source solution for this end-to-end workflow across standard
telemetry backends. Sentinel explores that gap with a focused, inspectable
system: it turns Prometheus alerts into incidents, retrieves related evidence
from Jaeger and OpenSearch, and gives a Codex reasoning agent bounded, read-only
tools for producing an evidence-cited investigation report.

The current implementation integrates with the OpenTelemetry Demo and focuses
on operation-level failures and service dependency failures.

## What Sentinel does

- Prometheus monitors operation-level RED signals—request rate, error ratio, and
  p95 latency—and raises alerts for abnormal behavior.
- Sentinel consumes those alerts and identifies failures inside services as well
  as dependency failures observed by callers.
- Related alerts are grouped into durable incidents, which create asynchronous
  investigation jobs after a short quiet period.
- The investigation worker collects an initial set of relevant traces and logs,
  then gives the incident and evidence to a Codex reasoning agent.
- If more context is needed, Codex requests additional evidence through bounded
  FastMCP tools backed by read-only telemetry adapters.
- Sentinel validates the report's evidence citations and stores the incident,
  job, evidence, and final report.

## Architecture

![Sentinel architecture](docs/sentinel-architecture.svg)

Instrumented services send telemetry through the OpenTelemetry Collector.
Prometheus stores metrics and evaluates Sentinel's detection rules, while Jaeger
stores traces and OpenSearch stores logs. Sentinel consumes firing alerts,
groups them into incidents, and schedules asynchronous investigations. The
investigation worker gathers initial evidence and invokes Codex, which can use
read-only tools to retrieve more context before producing its report.

## Components

### Detection

Prometheus converts span metrics into operation-level RED signals. Server-side
signals retain the service and operation names. Client-side signals also retain
the dependency being called, allowing Sentinel to detect a service that has
stopped producing its own telemetry but is still failing from a caller's point
of view.

PromQL alert rules combine high safety thresholds with rolling baselines. The
Sentinel detector polls only alerts labeled `sentinel="true"` and converts each
one into a consistent internal alert record.

### Incidents and investigation jobs

Sentinel groups active alert records around the affected service. Repeated polls
update an existing trigger rather than creating duplicates, and an incident is
resolved after all of its triggers become inactive.

When an incident gains a new trigger, Sentinel creates or postpones one durable
investigation job. A short quiet period gives related alerts time to join the
incident before investigation begins, while a maximum wait prevents indefinite
delay.

### Evidence collection

The investigation worker builds an initial evidence set from the exact alerts
attached to the job. Through read-only adapters, it queries Jaeger for relevant
traces, follows trace IDs into OpenSearch, and adds a bounded sample of nearby
warning and error logs. Every saved item receives an evidence ID that can be
cited in the final report.

### Agent investigation

Codex receives the incident and initial evidence as structured context. If that
context is insufficient, it can call FastMCP tools to search bounded time ranges
for additional traces and logs. The tools use the same read-only Jaeger and
OpenSearch adapters as initial evidence collection; the agent never receives
shell access or unrestricted access to the monitored application.

Before persistence, Sentinel validates that the report separates observations,
hypotheses, and unknowns and that every cited evidence ID actually exists.

### State and deployment

SQLite stores incident lifecycles, investigation jobs, retrieved evidence, and
reports. The detector and investigator run as separate processes from the same
Sentinel image and share the same data volume. This keeps detection running
independently while investigations execute asynchronously.

The OpenTelemetry Demo integration lives in
[integration/otel-demo](integration/otel-demo/README.md). It adds Sentinel and
its telemetry configuration without modifying the adjacent upstream demo
checkout.

## Running locally

### Prerequisites

- Docker Desktop with Docker Compose v2
- An adjacent checkout of the OpenTelemetry Demo

The expected directory layout is:

```text
AIOps_project/
├── opentelemetry-demo/
└── Sentinel/
```

From the `opentelemetry-demo` directory, start the demo and Sentinel detector
as one Compose project:

```bash
docker compose \
  --env-file .env \
  --env-file .env.override \
  -f compose.yaml \
  -f compose.observability.yaml \
  -f compose.extras.yaml \
  -f ../Sentinel/integration/otel-demo/compose.sentinel.yaml \
  up --force-recreate --remove-orphans --detach
```

To also start the asynchronous investigation worker, add
`--profile investigation` before `up`. The worker uses the same Sentinel image
and SQLite volume as the detector and maintains a separate volume for Codex
authentication.

### Inspecting Sentinel

The CLI is available inside the running Sentinel containers:

```bash
# Verify Prometheus connectivity and Sentinel's configuration
docker exec sentinel sentinel --config /etc/sentinel/config.json check

# View current operation-level RED metrics
docker exec sentinel sentinel --config /etc/sentinel/config.json metrics snapshot

# View firing alerts and grouped incidents
docker exec sentinel sentinel --config /etc/sentinel/config.json alerts list
docker exec sentinel sentinel --config /etc/sentinel/config.json incidents list

# View investigation jobs, retrieved evidence, and reports
docker exec sentinel sentinel --config /etc/sentinel/config.json investigations list
docker exec sentinel sentinel --config /etc/sentinel/config.json investigations show JOB_ID
```

For local Python development:

```bash
cp configs/local.example.json configs/local.json
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests -v
```

## Current status

Sentinel currently includes:

- OpenTelemetry Demo integration through a Compose override
- Operation-level server and client-dependency RED metrics
- Fixed-threshold and rolling-baseline Prometheus alerts
- Alert polling, incident grouping, recovery tracking, and durable SQLite state
- Quiet-period investigation scheduling
- Bounded Jaeger and OpenSearch adapters
- Initial evidence collection and trace-to-log correlation
- A FastMCP telemetry tool server and Codex investigation worker
- Evidence-citation validation and report persistence
- Unit tests for detection, incidents, telemetry adapters, evidence collection,
  investigation jobs, workers, and report validation

## Known limitations

- Detection and investigation quality are limited by the coverage, labels, and
  accuracy of the available telemetry.
- The current Prometheus rules and dependency mappings are configured for the
  OpenTelemetry Demo rather than discovered automatically.
- Rolling mean and standard-deviation alerts provide a simple adaptive baseline,
  not a complete time-series anomaly-detection model.
- Incident grouping currently centers on the affected service and may combine
  related-looking failures or separate parts of a larger incident.
- Sentinel identifies likely failing services and operations from telemetry; it
  does not claim source-code-level root causes without supporting evidence.
- SQLite and the local Compose deployment are intended for a single-node first
  version, not a highly available production deployment.

## Next steps

- Evaluate detection delay, false-alert rate, and investigation quality across
  repeatable injected-fault scenarios.
- Compare the current PromQL baselines with stronger time-series detection
  methods.
- Improve incident correlation using service dependencies and temporal
  relationships between alerts.
- Add deployment and change-event context to investigations.
- Generalize telemetry configuration and add adapters for additional backends.
