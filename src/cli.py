from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
from datetime import datetime
import json
import logging
from pathlib import Path
import sys
from typing import Any

from sentinel.config import SentinelConfig, load_config
from sentinel.detection.prometheus_alerts import PrometheusAlertSource
from sentinel.detection.runner import DetectionRunner
from sentinel.domain.errors import SentinelError
from sentinel.incidents.store import SQLiteIncidentStore
from sentinel.telemetry.prometheus import PrometheusAdapter
from sentinel.telemetry.red_queries import build_red_queries
from sentinel.telemetry.red_reader import RedMetricsReader


def main() -> None:
    parser = _parser()
    arguments = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if arguments.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = load_config(arguments.config)
        prometheus = PrometheusAdapter(
            config.prometheus.base_url,
            timeout_seconds=config.prometheus.timeout_seconds,
        )
        store = SQLiteIncidentStore(config.incidents.database_path)

        if arguments.command == "check":
            _check(prometheus, config)
        elif arguments.command == "metrics":
            _metrics_snapshot(prometheus, config)
        elif arguments.command == "alerts":
            _print_json(prometheus.firing_alerts())
        elif arguments.command == "detect":
            _detect(prometheus, store, config, arguments.once)
        elif arguments.command == "incidents":
            store.initialize()
            if arguments.incidents_command == "list":
                _print_json(store.list_incidents(arguments.status, arguments.limit))
            elif arguments.incidents_command == "show":
                incident = store.get_incident(arguments.incident_id)
                if incident is None:
                    parser.exit(1, f"error: incident {arguments.incident_id} not found\n")
                _print_json(incident)
    except (OSError, ValueError, SentinelError) as error:
        parser.exit(1, f"error: {error}\n")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sentinel", description="Operate the Sentinel detection service"
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("configs/local.json"),
        help="runtime configuration file (default: configs/local.json)",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="enable debug logging"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("check", help="check Prometheus connectivity and configuration")

    metrics = commands.add_parser("metrics", help="inspect standardized RED metrics")
    metrics_commands = metrics.add_subparsers(dest="metrics_command", required=True)
    metrics_commands.add_parser("snapshot", help="show current RED values by service")

    alerts = commands.add_parser("alerts", help="inspect Prometheus alerts")
    alert_commands = alerts.add_subparsers(dest="alerts_command", required=True)
    alert_commands.add_parser("list", help="show currently firing alerts")

    detect = commands.add_parser("detect", help="run continuous incident detection")
    detect.add_argument("--once", action="store_true", help="poll once and exit")

    incidents = commands.add_parser("incidents", help="inspect Sentinel incidents")
    incident_commands = incidents.add_subparsers(
        dest="incidents_command", required=True
    )
    incident_list = incident_commands.add_parser("list", help="list incidents")
    incident_list.add_argument("--status", choices=("open", "resolved"))
    incident_list.add_argument("--limit", type=int, default=100)
    incident_show = incident_commands.add_parser("show", help="show one incident")
    incident_show.add_argument("incident_id")
    return parser


def _check(prometheus: PrometheusAdapter, config: SentinelConfig) -> None:
    if not prometheus.ready():
        raise SentinelError(
            f"Prometheus is not ready at {config.prometheus.base_url}"
        )
    queries = build_red_queries(config.detection.services, config.detection.lookback)
    observations = RedMetricsReader(prometheus, queries).snapshot()
    observed_services = {observation.service for observation in observations}
    _print_json(
        {
            "prometheus": "ready",
            "base_url": config.prometheus.base_url,
            "configured_services": len(config.detection.services),
            "observed_services": len(observed_services),
            "observed_operations": len(observations),
            "database_path": str(config.incidents.database_path),
        }
    )


def _metrics_snapshot(
    prometheus: PrometheusAdapter, config: SentinelConfig
) -> None:
    queries = build_red_queries(config.detection.services, config.detection.lookback)
    _print_json(RedMetricsReader(prometheus, queries).snapshot())


def _detect(
    prometheus: PrometheusAdapter,
    store: SQLiteIncidentStore,
    config: SentinelConfig,
    once: bool,
) -> None:
    store.initialize()
    runner = DetectionRunner(
        PrometheusAlertSource(prometheus),
        store,
        poll_interval_seconds=config.detection.poll_interval_seconds,
        missing_polls_to_resolve=config.detection.missing_polls_to_resolve,
    )
    if once:
        _print_json(runner.run_once())
        return
    try:
        runner.run_forever()
    except KeyboardInterrupt:
        print("Sentinel detection stopped.", file=sys.stderr)


def _print_json(value: Any) -> None:
    print(json.dumps(_json_value(value), indent=2, sort_keys=True))


def _json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    return value


if __name__ == "__main__":
    main()
