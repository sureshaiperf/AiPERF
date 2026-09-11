"""Persist and query observed release outcomes for AiPERF executions.

This repository closes the learning loop between performance-test findings and
actual post-release behavior. Records are stored in the InfluxDB measurement
``aiperf_release_outcome`` and keyed by RUN_ID.

Examples:
    python release_outcome_repository.py record --run-id RUN_229 \
        --release-decision PROCEED --outcome SUCCESS \
        --production-status STABLE --incident-count 0 \
        --rollback-flag false --observation-window-days 14

    python release_outcome_repository.py get --run-id RUN_229
    python release_outcome_repository.py list --limit 20
    python release_outcome_repository.py summary --limit 100
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

try:
    from influxdb import InfluxDBClient
except ImportError:  # pragma: no cover
    InfluxDBClient = None  # type: ignore[assignment]

LOGGER = logging.getLogger("aiperf.release_outcome_repository")

MEASUREMENT = os.getenv(
    "AIPERF_RELEASE_OUTCOME_MEASUREMENT", "aiperf_release_outcome"
)
DEFAULT_HOST = os.getenv("INFLUX_HOST", "localhost")
DEFAULT_PORT = int(os.getenv("INFLUX_PORT", "8086"))
DEFAULT_DATABASE = os.getenv("INFLUX_DATABASE", "jmeter")
DEFAULT_TIMEOUT = int(os.getenv("INFLUX_TIMEOUT", "10"))
MAX_LIST_LIMIT = max(1, int(os.getenv("AIPERF_OUTCOME_MAX_LIMIT", "1000")))

OUTCOMES = {"SUCCESS", "FAILURE", "PARTIAL_SUCCESS", "NOT_OBSERVED"}
PRODUCTION_STATUSES = {
    "STABLE",
    "DEGRADED",
    "UNSTABLE",
    "ROLLED_BACK",
    "NOT_OBSERVED",
}
RELEASE_DECISIONS = {"PROCEED", "CONDITIONAL", "BLOCK", "UNKNOWN"}


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    result = str(value).strip()
    return result if result else default


def _finite_number(value: Any, field_name: str, minimum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field_name} must be numeric") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field_name} must be finite")
    if minimum is not None and result < minimum:
        raise ValueError(f"{field_name} must be greater than or equal to {minimum}")
    return result


def _non_negative_integer(value: Any, field_name: str) -> int:
    number = _finite_number(value, field_name, 0.0)
    if not number.is_integer():
        raise ValueError(f"{field_name} must be a whole number")
    return int(number)


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    normalized = _text(value).lower()
    if normalized in {"true", "1", "yes", "y", "on"}:
        return True
    if normalized in {"false", "0", "no", "n", "off"}:
        return False
    raise ValueError("rollback_flag must be true or false")


def _safe_run_id(value: Any) -> str:
    run_id = _text(value)
    if not run_id:
        raise ValueError("RUN_ID is required")
    if not re.fullmatch(r"[A-Za-z0-9_.:/-]+", run_id):
        raise ValueError(
            "RUN_ID contains unsupported characters; allowed characters are "
            "letters, numbers, underscore, hyphen, period, colon, and slash"
        )
    return run_id


def _normalize_choice(value: Any, allowed: set[str], field_name: str) -> str:
    normalized = _text(value).upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "GO": "PROCEED",
        "NO_GO": "BLOCK",
        "NOGO": "BLOCK",
        "PASS": "SUCCESS",
        "PASSED": "SUCCESS",
        "FAIL": "FAILURE",
        "FAILED": "FAILURE",
        "PARTIAL": "PARTIAL_SUCCESS",
        "HEALTHY": "STABLE",
        "NORMAL": "STABLE",
        "ROLLBACK": "ROLLED_BACK",
        "UNKNOWN": "NOT_OBSERVED" if field_name != "release_decision" else "UNKNOWN",
        "UNAVAILABLE": "NOT_OBSERVED" if field_name != "release_decision" else "UNKNOWN",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{field_name} must be one of: {choices}")
    return normalized


def _utc_iso(value: str | None = None) -> str:
    if not value:
        return datetime.now(timezone.utc).isoformat()
    candidate = value.strip()
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise ValueError(
            "observed_at must be ISO-8601, for example 2026-09-12T00:00:00+05:30"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _escape_string_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


@dataclass(frozen=True)
class ReleaseOutcome:
    run_id: str
    release_decision: str
    outcome: str
    production_status: str
    incident_count: int
    rollback_flag: bool
    production_p95: float
    production_error_rate: float
    observation_window_days: int
    observed_at: str
    notes: str = ""
    source: str = "MANUAL"
    schema_version: str = "1.0"

    @classmethod
    def create(
        cls,
        *,
        run_id: Any,
        release_decision: Any,
        outcome: Any,
        production_status: Any,
        incident_count: Any = 0,
        rollback_flag: Any = False,
        production_p95: Any = 0.0,
        production_error_rate: Any = 0.0,
        observation_window_days: Any = 0,
        observed_at: str | None = None,
        notes: Any = "",
        source: Any = "MANUAL",
    ) -> "ReleaseOutcome":
        normalized_outcome = _normalize_choice(outcome, OUTCOMES, "outcome")
        normalized_status = _normalize_choice(
            production_status, PRODUCTION_STATUSES, "production_status"
        )
        normalized_rollback = _boolean(rollback_flag)
        normalized_incidents = _non_negative_integer(incident_count, "incident_count")

        if normalized_rollback:
            normalized_outcome = "FAILURE"
            normalized_status = "ROLLED_BACK"

        if normalized_status == "ROLLED_BACK":
            normalized_rollback = True
            normalized_outcome = "FAILURE"

        if normalized_outcome == "SUCCESS" and normalized_rollback:
            raise ValueError("A successful outcome cannot have rollback_flag=true")

        return cls(
            run_id=_safe_run_id(run_id),
            release_decision=_normalize_choice(
                release_decision, RELEASE_DECISIONS, "release_decision"
            ),
            outcome=normalized_outcome,
            production_status=normalized_status,
            incident_count=normalized_incidents,
            rollback_flag=normalized_rollback,
            production_p95=_finite_number(production_p95, "production_p95", 0.0),
            production_error_rate=_finite_number(
                production_error_rate, "production_error_rate", 0.0
            ),
            observation_window_days=_non_negative_integer(
                observation_window_days, "observation_window_days"
            ),
            observed_at=_utc_iso(observed_at),
            notes=_text(notes),
            source=_text(source, "MANUAL").upper(),
        )

    def to_point(self) -> dict[str, Any]:
        return {
            "measurement": MEASUREMENT,
            "tags": {
                "run_id": self.run_id,
                "outcome": self.outcome,
                "production_status": self.production_status,
                "release_decision": self.release_decision,
                "source": self.source,
            },
            "time": self.observed_at,
            "fields": {
                "incident_count": int(self.incident_count),
                "rollback_flag": bool(self.rollback_flag),
                "production_p95": float(self.production_p95),
                "production_error_rate": float(self.production_error_rate),
                "observation_window_days": int(self.observation_window_days),
                "notes": self.notes or "N/A",
                "observed_at": self.observed_at,
                "schema_version": self.schema_version,
            },
        }


class ReleaseOutcomeRepository:
    """InfluxDB-backed repository for observed AiPERF release outcomes."""

    def __init__(self, client: Any, measurement: str = MEASUREMENT) -> None:
        if client is None:
            raise ValueError("InfluxDB client is required")
        self.client = client
        self.measurement = measurement

    def health_check(self) -> None:
        try:
            self.client.ping()
        except Exception as exc:
            raise RuntimeError("Unable to connect to InfluxDB") from exc

    def save(
        self,
        outcome: ReleaseOutcome,
        *,
        overwrite: bool = True,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        existing = self.get(outcome.run_id)
        if existing and not overwrite:
            raise RuntimeError(
                f"Outcome already exists for RUN_ID {outcome.run_id}; use --overwrite"
            )

        point = outcome.to_point()
        point["measurement"] = self.measurement
        if dry_run:
            return {"action": "DRY_RUN", "replaced": bool(existing), "point": point}

        if existing and overwrite:
            self.delete(outcome.run_id)

        try:
            written = self.client.write_points([point], time_precision="ms")
        except Exception as exc:
            raise RuntimeError(
                f"Failed to persist outcome for RUN_ID {outcome.run_id}"
            ) from exc
        if written is False:
            raise RuntimeError(
                f"InfluxDB rejected outcome for RUN_ID {outcome.run_id}"
            )

        persisted = self.get(outcome.run_id)
        if not persisted:
            raise RuntimeError(
                f"Outcome write could not be verified for RUN_ID {outcome.run_id}"
            )
        return {"action": "UPDATED" if existing else "CREATED", "record": persisted}

    def get(self, run_id: str) -> dict[str, Any] | None:
        safe_run_id = _safe_run_id(run_id)
        query = (
            f'SELECT * FROM "{self.measurement}" '
            f"WHERE run_id='{_escape_string_literal(safe_run_id)}' "
            "ORDER BY time DESC LIMIT 1"
        )
        rows = self._query(query)
        return self._normalize_row(rows[0]) if rows else None

    def list(self, *, limit: int = 100) -> list[dict[str, Any]]:
        bounded = min(max(int(limit), 0), MAX_LIST_LIMIT)
        if bounded == 0:
            return []
        rows = self._query(
            f'SELECT * FROM "{self.measurement}" ORDER BY time DESC LIMIT {bounded}'
        )
        output: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            normalized = self._normalize_row(row)
            run_id = _text(normalized.get("run_id"))
            if run_id and run_id not in seen:
                seen.add(run_id)
                output.append(normalized)
        return output

    def delete(self, run_id: str) -> bool:
        safe_run_id = _safe_run_id(run_id)
        query = (
            f'DELETE FROM "{self.measurement}" '
            f"WHERE run_id='{_escape_string_literal(safe_run_id)}'"
        )
        try:
            self.client.query(query)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to delete outcome for RUN_ID {safe_run_id}"
            ) from exc
        return self.get(safe_run_id) is None

    def summary(self, *, limit: int = 100) -> dict[str, Any]:
        records = self.list(limit=limit)
        counts = {label: 0 for label in sorted(OUTCOMES)}
        for record in records:
            label = _text(record.get("outcome"), "NOT_OBSERVED").upper()
            counts[label if label in counts else "NOT_OBSERVED"] += 1

        observed = counts["SUCCESS"] + counts["FAILURE"] + counts["PARTIAL_SUCCESS"]
        success_rate = counts["SUCCESS"] / observed * 100.0 if observed else 0.0
        weighted_rate = (
            (counts["SUCCESS"] + 0.5 * counts["PARTIAL_SUCCESS"])
            / observed
            * 100.0
            if observed
            else 0.0
        )
        return {
            "measurement": self.measurement,
            "total_records": len(records),
            "observed_records": observed,
            "outcome_counts": counts,
            "success_rate": round(success_rate, 2),
            "weighted_success_rate": round(weighted_rate, 2),
            "rollback_count": sum(bool(row.get("rollback_flag")) for row in records),
            "incident_count": sum(
                _non_negative_integer(row.get("incident_count", 0), "incident_count")
                for row in records
            ),
        }

    def _query(self, query: str) -> list[dict[str, Any]]:
        try:
            result = self.client.query(query)
            return [dict(row) for row in result.get_points()]
        except Exception as exc:
            raise RuntimeError(f"InfluxDB query failed: {query}") from exc

    @staticmethod
    def _normalize_row(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "time": _text(row.get("time")),
            "run_id": _text(row.get("run_id")),
            "release_decision": _text(row.get("release_decision"), "UNKNOWN"),
            "outcome": _text(row.get("outcome"), "NOT_OBSERVED"),
            "production_status": _text(
                row.get("production_status"), "NOT_OBSERVED"
            ),
            "incident_count": _non_negative_integer(
                row.get("incident_count", 0), "incident_count"
            ),
            "rollback_flag": _boolean(row.get("rollback_flag", False)),
            "production_p95": _finite_number(
                row.get("production_p95", 0.0), "production_p95", 0.0
            ),
            "production_error_rate": _finite_number(
                row.get("production_error_rate", 0.0),
                "production_error_rate",
                0.0,
            ),
            "observation_window_days": _non_negative_integer(
                row.get("observation_window_days", 0), "observation_window_days"
            ),
            "observed_at": _text(row.get("observed_at") or row.get("time")),
            "notes": _text(row.get("notes")),
            "source": _text(row.get("source"), "MANUAL"),
            "schema_version": _text(row.get("schema_version"), "1.0"),
        }


def create_client(args: argparse.Namespace) -> Any:
    if InfluxDBClient is None:
        raise RuntimeError(
            "The influxdb package is not installed. Install it with: pip install influxdb"
        )
    return InfluxDBClient(
        host=args.host,
        port=args.port,
        username=args.username,
        password=args.password,
        database=args.database,
        ssl=args.ssl,
        verify_ssl=args.verify_ssl,
        timeout=args.timeout,
    )


def _add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--database", default=DEFAULT_DATABASE)
    parser.add_argument("--username", default=os.getenv("INFLUX_USERNAME"))
    parser.add_argument("--password", default=os.getenv("INFLUX_PASSWORD"))
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--ssl", action=argparse.BooleanOptionalAction, default=False
    )
    parser.add_argument(
        "--verify-ssl", action=argparse.BooleanOptionalAction, default=True
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Persist and query AiPERF observed release outcomes."
    )
    parser.add_argument(
        "--log-level",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        default=os.getenv("AIPERF_LOG_LEVEL", "INFO").upper(),
    )
    _add_connection_arguments(parser)
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="Create or replace an outcome")
    record.add_argument("--run-id", default=os.getenv("RUN_ID"), required=False)
    record.add_argument("--release-decision", required=True)
    record.add_argument("--outcome", required=True)
    record.add_argument("--production-status", required=True)
    record.add_argument("--incident-count", default=0)
    record.add_argument("--rollback-flag", default="false")
    record.add_argument("--production-p95", default=0.0)
    record.add_argument("--production-error-rate", default=0.0)
    record.add_argument("--observation-window-days", default=0)
    record.add_argument("--observed-at")
    record.add_argument("--notes", default="")
    record.add_argument("--source", default="MANUAL")
    record.add_argument(
        "--overwrite", action=argparse.BooleanOptionalAction, default=True
    )
    record.add_argument("--dry-run", action="store_true")

    get_parser = subparsers.add_parser("get", help="Get the latest outcome for a RUN_ID")
    get_parser.add_argument("--run-id", default=os.getenv("RUN_ID"), required=False)

    list_parser = subparsers.add_parser("list", help="List latest outcomes")
    list_parser.add_argument("--limit", type=int, default=100)

    summary_parser = subparsers.add_parser("summary", help="Summarize outcomes")
    summary_parser.add_argument("--limit", type=int, default=100)

    delete_parser = subparsers.add_parser("delete", help="Delete a RUN_ID outcome")
    delete_parser.add_argument("--run-id", default=os.getenv("RUN_ID"), required=False)

    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    client = create_client(args)
    repository = ReleaseOutcomeRepository(client)
    try:
        repository.health_check()
        if args.command == "record":
            outcome = ReleaseOutcome.create(
                run_id=args.run_id,
                release_decision=args.release_decision,
                outcome=args.outcome,
                production_status=args.production_status,
                incident_count=args.incident_count,
                rollback_flag=args.rollback_flag,
                production_p95=args.production_p95,
                production_error_rate=args.production_error_rate,
                observation_window_days=args.observation_window_days,
                observed_at=args.observed_at,
                notes=args.notes,
                source=args.source,
            )
            return repository.save(
                outcome, overwrite=args.overwrite, dry_run=args.dry_run
            )
        if args.command == "get":
            record = repository.get(_safe_run_id(args.run_id))
            return {"found": record is not None, "record": record}
        if args.command == "list":
            records = repository.list(limit=args.limit)
            return {"count": len(records), "records": records}
        if args.command == "summary":
            return repository.summary(limit=args.limit)
        if args.command == "delete":
            run_id = _safe_run_id(args.run_id)
            return {"run_id": run_id, "deleted": repository.delete(run_id)}
        raise RuntimeError(f"Unsupported command: {args.command}")
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    try:
        result = run(args)
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
        return 0
    except (ValueError, RuntimeError) as exc:
        LOGGER.error("%s", exc)
        return 2
    except KeyboardInterrupt:
        LOGGER.error("Operation cancelled")
        return 130
    except Exception:
        LOGGER.exception("Unexpected release outcome repository failure")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
