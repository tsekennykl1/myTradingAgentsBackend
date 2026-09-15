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


def get_snapshot(snapshot_key: str) -> Optional[dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM snapshots WHERE snapshot_key = ?",
            (snapshot_key,),
        ).fetchone()

    if not row:
        return None

    return {
        "snapshot_key": row["snapshot_key"],
        "ticker": row["ticker"],
        "analysis_date": row["analysis_date"],
        "summary_cards": _json_loads(row["summary_cards_json"], {}),
        "market_data_json": row["market_data_json"],
        "price_rows": _json_loads(row["price_rows_json"], []),
        "chart_html": row["chart_html"],
        "chart_png": row["chart_png"],
        "warnings": _json_loads(row["warnings_json"], []),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def upsert_snapshot(record: dict[str, Any]) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO snapshots (
                snapshot_key, ticker, analysis_date, summary_cards_json, market_data_json,
                price_rows_json, chart_html, chart_png, warnings_json, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(snapshot_key) DO UPDATE SET
                ticker = excluded.ticker,
                analysis_date = excluded.analysis_date,
                summary_cards_json = excluded.summary_cards_json,
                market_data_json = excluded.market_data_json,
                price_rows_json = excluded.price_rows_json,
                chart_html = excluded.chart_html,
                chart_png = excluded.chart_png,
                warnings_json = excluded.warnings_json,
                updated_at = excluded.updated_at
            """,
            (
                record["snapshot_key"],
                record["ticker"],
                record["analysis_date"],
                _json_dumps(record.get("summary_cards", {})),
                record.get("market_data_json"),
                _json_dumps(record.get("price_rows", [])),
                record.get("chart_html"),
                record.get("chart_png"),
                _json_dumps(record.get("warnings", [])),
                record["created_at"],
                record["updated_at"],
            ),
        )