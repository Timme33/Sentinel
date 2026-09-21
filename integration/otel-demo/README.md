# OpenTelemetry Demo integration

This integration attaches Sentinel to the sibling `opentelemetry-demo` checkout
without modifying that upstream repository. It replaces the demo Prometheus
configuration at container runtime, mounts Sentinel's recording and alerting
rules, enriches client spans with bounded dependency identities, and adds the
Sentinel detector service to the existing Compose project.

The expected local layout is:

```text
AIOps_project/
├── opentelemetry-demo/
└── Sentinel/
```

Run Compose from the `opentelemetry-demo` directory so relative paths and the
demo's environment files resolve consistently:

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

The Sentinel Prometheus configuration is derived from the demo configuration
present in the adjacent checkout on September 19, 2026. Compare the files again
before using this integration with a newer demo revision.

The integration also mounts `otelcol-sentinel.yml` and therefore recreates the
OTel Collector when first applied. To update only the affected services after
the demo is already running, target `otel-collector`, `prometheus`, and
`sentinel` with the same Compose file list.
