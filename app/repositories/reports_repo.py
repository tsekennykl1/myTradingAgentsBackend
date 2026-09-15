from __future__ import annotations

import json
from datetime import datetime, timezone
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


def get_report(report_key: str) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM reports WHERE report_key = ?",
            (report_key,),
        ).fetchone()

    if not row:
        return None

    return {
        "report_key": row["report_key"],
        "snapshot_key": row["snapshot_key"],
        "ticker": row["ticker"],
        "analysis_date": row["analysis_date"],
        "payload": _json_loads(row["payload_json"], {}),
        "result": _json_loads(row["result_json"], {}),
        "created_at": row["created_at"],
        "expires_at": row["expires_at"],
    }


def get_fresh_report(report_key: str, now_dt: datetime) -> Optional[dict[str, Any]]:
    report = get_report(report_key)
    if not report:
        return None

    try:
        expires_at = datetime.fromisoformat(report["expires_at"])
    except Exception:
        return None

    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at < now_dt:
        return None

    return report


def upsert_report(record: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO reports (
                report_key, snapshot_key, ticker, analysis_date, payload_json,
                result_json, created_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(report_key) DO UPDATE SET
                snapshot_key = excluded.snapshot_key,
                ticker = excluded.ticker,
                analysis_date = excluded.analysis_date,
                payload_json = excluded.payload_json,
                result_json = excluded.result_json,
                created_at = excluded.created_at,
                expires_at = excluded.expires_at
            """,
            (
                record["report_key"],
                record["snapshot_key"],
                record["ticker"],
                record["analysis_date"],
                _json_dumps(record.get("payload", {})),
                _json_dumps(record.get("result", {})),
                record["created_at"],
                record["expires_at"],
            ),
        )