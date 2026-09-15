from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "app.db"


print("DB path:", Path(DB_PATH).resolve())
print("Exists:", Path(DB_PATH).exists())
print("Is file:", Path(DB_PATH).is_file())

def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with get_conn() as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA synchronous=NORMAL;")

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                report_key TEXT,
                snapshot_key TEXT,
                source TEXT NOT NULL,
                status TEXT NOT NULL,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                poll_after_ms INTEGER NOT NULL DEFAULT 1500,

                payload_json TEXT NOT NULL,
                result_json TEXT,
                partial_result_json TEXT NOT NULL,
                error_json TEXT,
                progress_json TEXT NOT NULL,
                logs_json TEXT NOT NULL,
                capabilities_json TEXT NOT NULL,
                timings_json TEXT NOT NULL,

                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_runs_status
            ON runs(status)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_runs_report_key_status
            ON runs(report_key, status)
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                snapshot_key TEXT PRIMARY KEY,
                ticker TEXT NOT NULL,
                analysis_date TEXT NOT NULL,

                summary_cards_json TEXT NOT NULL,
                market_data_json TEXT,
                price_rows_json TEXT,
                chart_html TEXT,
                chart_png BLOB,
                warnings_json TEXT NOT NULL,

                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_snapshots_ticker_date
            ON snapshots(ticker, analysis_date)
            """
        )

        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                report_key TEXT PRIMARY KEY,
                snapshot_key TEXT NOT NULL,
                ticker TEXT NOT NULL,
                analysis_date TEXT NOT NULL,

                payload_json TEXT NOT NULL,
                result_json TEXT NOT NULL,

                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,

                FOREIGN KEY(snapshot_key) REFERENCES snapshots(snapshot_key)
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_reports_expires_at
            ON reports(expires_at)
            """
        )

        conn.commit()


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()