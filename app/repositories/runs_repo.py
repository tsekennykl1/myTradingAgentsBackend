from __future__ import annotations

import json
from typing import Any, Optional

from app.db import get_conn


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _row_to_run(row: Any) -> dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "report_key": row["report_key"],
        "snapshot_key": row["snapshot_key"],
        "source": row["source"],
        "status": row["status"],
        "cancel_requested": bool(row["cancel_requested"]),
        "poll_after_ms": row["poll_after_ms"],
        "payload": _json_loads(row["payload_json"], {}),
        "result": _json_loads(row["result_json"], None),
        "partial_result": _json_loads(row["partial_result_json"], {}),
        "error": _json_loads(row["error_json"], None),
        "progress": _json_loads(row["progress_json"], {}),
        "logs": _json_loads(row["logs_json"], []),
        "capabilities": _json_loads(row["capabilities_json"], {}),
        "timings": _json_loads(row["timings_json"], {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
    }


def create_run(record: dict[str, Any]) -> str:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO runs (
                run_id, report_key, snapshot_key, source, status, cancel_requested, poll_after_ms,
                payload_json, result_json, partial_result_json, error_json, progress_json, logs_json,
                capabilities_json, timings_json, created_at, updated_at, started_at, completed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["run_id"],
                record.get("report_key"),
                record.get("snapshot_key"),
                record["source"],
                record["status"],
                1 if record.get("cancel_requested") else 0,
                int(record.get("poll_after_ms", 1500)),
                _json_dumps(record.get("payload", {})),
                _json_dumps(record.get("result")) if record.get("result") is not None else None,
                _json_dumps(record.get("partial_result", {})),
                _json_dumps(record.get("error")) if record.get("error") is not None else None,
                _json_dumps(record.get("progress", {})),
                _json_dumps(record.get("logs", [])),
                _json_dumps(record.get("capabilities", {})),
                _json_dumps(record.get("timings", {})),
                record["created_at"],
                record["updated_at"],
                record.get("started_at"),
                record.get("completed_at"),
            ),
        )
    return record["run_id"]


def get_run(run_id: str) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    return _row_to_run(row) if row else None


def list_runs() -> list[dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY created_at DESC"
        ).fetchall()
    return [_row_to_run(row) for row in rows]


def update_run(run_id: str, **updates: Any) -> bool:
    current = get_run(run_id)
    if not current:
        return False

    merged = dict(current)
    merged.update(updates)

    with get_conn() as conn:
        conn.execute(
            """
            UPDATE runs
            SET
                report_key = ?,
                snapshot_key = ?,
                source = ?,
                status = ?,
                cancel_requested = ?,
                poll_after_ms = ?,
                payload_json = ?,
                result_json = ?,
                partial_result_json = ?,
                error_json = ?,
                progress_json = ?,
                logs_json = ?,
                capabilities_json = ?,
                timings_json = ?,
                created_at = ?,
                updated_at = ?,
                started_at = ?,
                completed_at = ?
            WHERE run_id = ?
            """,
            (
                merged.get("report_key"),
                merged.get("snapshot_key"),
                merged["source"],
                merged["status"],
                1 if merged.get("cancel_requested") else 0,
                int(merged.get("poll_after_ms", 1500)),
                _json_dumps(merged.get("payload", {})),
                _json_dumps(merged.get("result")) if merged.get("result") is not None else None,
                _json_dumps(merged.get("partial_result", {})),
                _json_dumps(merged.get("error")) if merged.get("error") is not None else None,
                _json_dumps(merged.get("progress", {})),
                _json_dumps(merged.get("logs", [])),
                _json_dumps(merged.get("capabilities", {})),
                _json_dumps(merged.get("timings", {})),
                merged["created_at"],
                merged["updated_at"],
                merged.get("started_at"),
                merged.get("completed_at"),
                run_id,
            ),
        )
    return True


def find_active_run_by_report_key(report_key: str) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT * FROM runs
            WHERE report_key = ?
              AND status IN (
                  'queued',
                  'initializing',
                  'preparing_inputs',
                  'loading_market_data',
                  'building_charts',
                  'importing_engine',
                  'running',
                  'running_ai_analysis'
              )
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (report_key,),
        ).fetchone()
    return _row_to_run(row) if row else None


def cancel_run(run_id: str, updated_at: str) -> bool:
    run = get_run(run_id)
    if not run:
        return False

    if run["status"] in {"completed", "failed", "cancelled"}:
        return True

    logs = list(run.get("logs") or [])
    logs.append(
        {
            "ts": updated_at,
            "level": "warning",
            "message": "Cancellation requested.",
        }
    )

    return update_run(
        run_id,
        cancel_requested=True,
        logs=logs[-200:],
        updated_at=updated_at,
    )


def reconcile_incomplete_runs(now_iso: str) -> None:
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT * FROM runs
            WHERE status IN (
                'queued',
                'initializing',
                'preparing_inputs',
                'loading_market_data',
                'building_charts',
                'importing_engine',
                'running',
                'running_ai_analysis'
            )
              AND completed_at IS NULL
            """
        ).fetchall()

    for row in rows:
        run = _row_to_run(row)
        logs = list(run.get("logs") or [])
        logs.append(
            {
                "ts": now_iso,
                "level": "warning",
                "message": "Run marked failed during startup reconciliation.",
            }
        )
        update_run(
            run["run_id"],
            status="failed",
            completed_at=now_iso,
            updated_at=now_iso,
            error={
                "message": "Run was interrupted by a server restart or worker shutdown before completion.",
                "code": "orphaned_run",
            },
            progress={
                "stage": "failed",
                "message": "Run interrupted before completion.",
                "percent": 100,
                "step": 7,
                "total_steps": 7,
            },
            poll_after_ms=0,
            logs=logs[-200:],
        )