from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

import pandas as pd

from app.artifacts import (
    write_all_core_artifacts,
    write_market_data_json_artifact,
    write_price_chart_html_artifact,
    write_price_chart_json_artifact,
    write_price_chart_png_artifact,
    write_result_html_artifact,
)
from app.market_data import download_price_frame
from app.tradingagents_service import (
    engine_is_available,
    normalize_engine_config,
    run_tradingagents,
)

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "app.db"

_DB_LOCK = threading.Lock()


MODEL_ALIASES = {
    "DeepSeek V4 Flash": "deepseek-v4-flash",
    "DeepSeek V4 Pro": "deepseek-v4-pro",
    "DeepSeek Flash": "deepseek-v4-flash",
    "DeepSeek Pro": "deepseek-v4-pro",
    "deepseek-v4": "deepseek-v4-pro",
    "deepseek-chat": "deepseek-v4-flash",
    "deepseek chat": "deepseek-v4-flash",
    "deepseek-flash": "deepseek-v4-flash",
    "deepseek-pro": "deepseek-v4-pro",
}

PROVIDER_ALIASES = {
    "DeepSeek": "deepseek",
    "OpenAI": "openai",
    "Anthropic": "anthropic",
    "Google": "google",
    "xAI": "xai",
    "XAI": "xai",
}


def validate_provider_env_or_raise(provider: str) -> None:
    provider = (provider or "").strip().lower()

    provider_env_map = {
        "openai": ["OPENAI_API_KEY"],
        "google": ["GOOGLE_API_KEY"],
        "anthropic": ["ANTHROPIC_API_KEY"],
        "xai": ["XAI_API_KEY"],
        "deepseek": ["DEEPSEEK_API_KEY"],
        "qwen": ["DASHSCOPE_API_KEY"],
        "qwen_cn": ["DASHSCOPE_CN_API_KEY"],
        "glm": ["ZHIPU_API_KEY"],
        "glm_cn": ["ZHIPU_CN_API_KEY"],
        "minimax": ["MINIMAX_API_KEY"],
        "minimax_cn": ["MINIMAX_CN_API_KEY"],
        "openrouter": ["OPENROUTER_API_KEY"],
        "azure_openai": [
            "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_ENDPOINT",
            "AZURE_OPENAI_API_VERSION",
        ],
        "ollama": ["OLLAMA_BASE_URL"],
        "openai_compatible": ["TRADINGAGENTS_LLM_BACKEND_URL"],
        "bedrock": ["AWS_DEFAULT_REGION"],
        "azure": [
            "AZURE_OPENAI_API_KEY",
            "AZURE_OPENAI_ENDPOINT",
            "AZURE_OPENAI_API_VERSION",
        ],
    }

    required = provider_env_map.get(provider, [])
    missing = [key for key in required if not os.getenv(key, "").strip()]

    if provider == "openai_compatible":
        requires_api_key = os.getenv(
            "OPENAI_COMPATIBLE_REQUIRES_API_KEY", "false"
        ).strip().lower() == "true"
        if requires_api_key and not os.getenv("OPENAI_COMPATIBLE_API_KEY", "").strip():
            missing.append("OPENAI_COMPATIBLE_API_KEY")

    if missing:
        missing_str = ", ".join(missing)
        raise ValueError(
            f"Missing required environment variable(s) for provider '{provider}': {missing_str}"
        )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _now_perf() -> float:
    return time.perf_counter()


def _duration_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _ensure_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def _get_conn() -> sqlite3.Connection:
    _ensure_storage()
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db() -> None:
    with _DB_LOCK:
        conn = _get_conn()
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    error_json TEXT,
                    result_json TEXT,
                    partial_result_json TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    logs_json TEXT NOT NULL,
                    poll_after_ms INTEGER NOT NULL DEFAULT 1500,
                    capabilities_json TEXT NOT NULL,
                    progress_json TEXT NOT NULL,
                    timings_json TEXT NOT NULL,
                    payload_hash TEXT
                )
                """
            )

            existing_cols = {
                row["name"]
                for row in conn.execute("PRAGMA table_info(runs)").fetchall()
            }

            expected_columns = {
                "run_id": "TEXT",
                "status": "TEXT NOT NULL",
                "payload_json": "TEXT NOT NULL",
                "created_at": "TEXT NOT NULL",
                "updated_at": "TEXT NOT NULL",
                "started_at": "TEXT",
                "completed_at": "TEXT",
                "error_json": "TEXT",
                "result_json": "TEXT",
                "partial_result_json": "TEXT",
                "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
                "logs_json": "TEXT NOT NULL DEFAULT '[]'",
                "poll_after_ms": "INTEGER NOT NULL DEFAULT 1500",
                "capabilities_json": "TEXT NOT NULL DEFAULT '{}'",
                "progress_json": "TEXT NOT NULL DEFAULT '{}'",
                "timings_json": "TEXT NOT NULL DEFAULT '{}'",
                "payload_hash": "TEXT",
            }

            for col_name, col_def in expected_columns.items():
                if col_name not in existing_cols:
                    conn.execute(f"ALTER TABLE runs ADD COLUMN {col_name} {col_def}")

            conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_created_at ON runs(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_payload_hash ON runs(payload_hash)")

            rows = conn.execute(
                "SELECT run_id, payload_json, payload_hash FROM runs"
            ).fetchall()

            for row in rows:
                if row["payload_hash"]:
                    continue
                try:
                    payload = _json_loads(row["payload_json"], {})
                    normalized = _normalize_payload(payload)
                    payload_hash = _payload_hash(normalized)
                    conn.execute(
                        "UPDATE runs SET payload_hash = ? WHERE run_id = ?",
                        (payload_hash, row["run_id"]),
                    )
                except Exception:
                    pass

            conn.commit()
        finally:
            conn.close()


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: Optional[str], default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _row_to_run(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "status": row["status"],
        "payload": _json_loads(row["payload_json"], {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "error": _json_loads(row["error_json"], None),
        "result": _json_loads(row["result_json"], None),
        "partial_result": _json_loads(row["partial_result_json"], {}),
        "cancel_requested": bool(row["cancel_requested"]),
        "logs": _json_loads(row["logs_json"], []),
        "poll_after_ms": row["poll_after_ms"],
        "capabilities": _json_loads(row["capabilities_json"], {}),
        "progress": _json_loads(row["progress_json"], {}),
        "timings": _json_loads(row["timings_json"], {}),
    }


def _deep_merge_dict(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_dict(out[key], value)
        else:
            out[key] = value
    return out


def _trim_logs(logs: list[Dict[str, Any]], max_entries: int = 200) -> list[Dict[str, Any]]:
    if len(logs) <= max_entries:
        return logs
    return logs[-max_entries:]


def _capabilities() -> Dict[str, Any]:
    return {
        "fred_enabled": bool(os.getenv("FRED_API_KEY")),
        "engine_available": engine_available(),
    }


def _payload_hash(payload: Dict[str, Any]) -> str:
    normalized = _normalize_payload(payload)
    return json.dumps(normalized, sort_keys=True, ensure_ascii=False)


def _insert_run(run: Dict[str, Any], payload_hash: str) -> None:
    with _DB_LOCK:
        conn = _get_conn()
        try:
            conn.execute(
                """
                INSERT INTO runs (
                    run_id, status, payload_json, created_at, updated_at,
                    started_at, completed_at, error_json, result_json,
                    partial_result_json, cancel_requested, logs_json,
                    poll_after_ms, capabilities_json, progress_json,
                    timings_json, payload_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run["run_id"],
                    run["status"],
                    _json_dumps(run["payload"]),
                    run["created_at"],
                    run["updated_at"],
                    run["started_at"],
                    run["completed_at"],
                    _json_dumps(run["error"]),
                    _json_dumps(run["result"]),
                    _json_dumps(run["partial_result"]),
                    1 if run["cancel_requested"] else 0,
                    _json_dumps(run["logs"]),
                    run["poll_after_ms"],
                    _json_dumps(run["capabilities"]),
                    _json_dumps(run["progress"]),
                    _json_dumps(run["timings"]),
                    payload_hash,
                ),
            )
            conn.commit()
        finally:
            conn.close()


def _update_run(run_id: str, **updates: Any) -> bool:
    current = get_run(run_id)
    if not current:
        return False

    merged = _deep_merge_dict(current, updates)
    merged["updated_at"] = _utc_now()

    if isinstance(merged.get("logs"), list):
        merged["logs"] = _trim_logs(merged["logs"])

    with _DB_LOCK:
        conn = _get_conn()
        try:
            conn.execute(
                """
                UPDATE runs
                SET status = ?,
                    payload_json = ?,
                    updated_at = ?,
                    started_at = ?,
                    completed_at = ?,
                    error_json = ?,
                    result_json = ?,
                    partial_result_json = ?,
                    cancel_requested = ?,
                    logs_json = ?,
                    poll_after_ms = ?,
                    capabilities_json = ?,
                    progress_json = ?,
                    timings_json = ?,
                    payload_hash = ?
                WHERE run_id = ?
                """,
                (
                    merged["status"],
                    _json_dumps(merged["payload"]),
                    merged["updated_at"],
                    merged["started_at"],
                    merged["completed_at"],
                    _json_dumps(merged["error"]),
                    _json_dumps(merged["result"]),
                    _json_dumps(merged["partial_result"]),
                    1 if merged["cancel_requested"] else 0,
                    _json_dumps(merged["logs"]),
                    merged["poll_after_ms"],
                    _json_dumps(merged["capabilities"]),
                    _json_dumps(merged["progress"]),
                    _json_dumps(merged["timings"]),
                    _payload_hash(merged["payload"]),
                    run_id,
                ),
            )
            conn.commit()
            return True
        finally:
            conn.close()


def _append_log(
    run_id: str,
    message: str,
    level: str = "info",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    run = get_run(run_id)
    if not run:
        return

    logs = list(run.get("logs") or [])
    entry: Dict[str, Any] = {
        "ts": _utc_now(),
        "level": level,
        "message": message,
    }
    if extra:
        entry["extra"] = extra
    logs.append(entry)

    _update_run(run_id, logs=_trim_logs(logs))


def _set_progress(
    run_id: str,
    *,
    status: Optional[str] = None,
    stage: Optional[str] = None,
    message: Optional[str] = None,
    percent: Optional[int] = None,
    step: Optional[int] = None,
    total_steps: Optional[int] = None,
    poll_after_ms: Optional[int] = None,
) -> bool:
    updates: Dict[str, Any] = {}
    if status is not None:
        updates["status"] = status

    updates["progress"] = {
        "stage": stage,
        "message": message,
        "percent": percent,
        "step": step,
        "total_steps": total_steps,
    }

    if poll_after_ms is not None:
        updates["poll_after_ms"] = poll_after_ms

    return _update_run(run_id, **updates)


def _record_timing(run_id: str, name: str, duration_ms: int) -> bool:
    run = get_run(run_id)
    if not run:
        return False

    timings = dict(run.get("timings") or {})
    stage_durations = dict(timings.get("stage_durations_ms") or {})
    stage_durations[name] = duration_ms
    timings["stage_durations_ms"] = stage_durations
    timings["last_update_at"] = _utc_now()

    return _update_run(run_id, timings=timings)


def _increment_counter(run_id: str, name: str, amount: int = 1) -> bool:
    run = get_run(run_id)
    if not run:
        return False

    timings = dict(run.get("timings") or {})
    counters = dict(timings.get("counters") or {})
    counters[name] = int(counters.get(name, 0)) + amount
    timings["counters"] = counters
    timings["last_update_at"] = _utc_now()

    return _update_run(run_id, timings=timings)


def _set_partial_result(run_id: str, **fields: Any) -> bool:
    run = get_run(run_id)
    if not run:
        return False

    partial = dict(run.get("partial_result") or {})
    partial.update(fields)
    return _update_run(run_id, partial_result=partial)


def _add_warning(run_id: str, warning: str) -> None:
    run = get_run(run_id)
    if not run:
        return

    partial = dict(run.get("partial_result") or {})
    warnings = list(partial.get("warnings") or [])
    warnings.append(warning)
    partial["warnings"] = warnings[-50:]
    _update_run(run_id, partial_result=partial)


def reconcile_incomplete_runs() -> None:
    recoverable_statuses = {
        "queued",
        "initializing",
        "preparing_inputs",
        "loading_market_data",
        "building_charts",
        "importing_engine",
        "running",
    }

    with _DB_LOCK:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT * FROM runs WHERE completed_at IS NULL"
            ).fetchall()

            for row in rows:
                run = _row_to_run(row)
                if run["status"] in recoverable_statuses:
                    logs = list(run.get("logs") or [])
                    logs.append(
                        {
                            "ts": _utc_now(),
                            "level": "warning",
                            "message": "Run marked failed during startup reconciliation.",
                        }
                    )

                    conn.execute(
                        """
                        UPDATE runs
                        SET status = ?,
                            completed_at = ?,
                            updated_at = ?,
                            error_json = ?,
                            logs_json = ?
                        WHERE run_id = ?
                        """,
                        (
                            "failed",
                            _utc_now(),
                            _utc_now(),
                            _json_dumps(
                                {
                                    "message": "Run was interrupted by a server restart or worker shutdown before completion.",
                                    "code": "orphaned_run",
                                }
                            ),
                            _json_dumps(_trim_logs(logs)),
                            run["run_id"],
                        ),
                    )
            conn.commit()
        finally:
            conn.close()


def engine_available() -> bool:
    try:
        return engine_is_available()
    except Exception:
        return False


def list_runs() -> list[Dict[str, Any]]:
    _init_db()
    conn = _get_conn()
    try:
        rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
        return [_row_to_run(row) for row in rows]
    finally:
        conn.close()


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    _init_db()
    conn = _get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if not row:
            return None
        return _row_to_run(row)
    finally:
        conn.close()


def get_run_reports(run_id: str) -> Optional[Dict[str, Any]]:
    run = get_run(run_id)
    if not run:
        return None

    result = run.get("result") or {}
    partial = run.get("partial_result") or {}

    reports: Dict[str, Any] = {}
    if isinstance(result.get("reports"), dict):
        reports.update(result["reports"])
    elif isinstance(partial.get("reports"), dict):
        reports.update(partial["reports"])

    return reports


def get_run_report(run_id: str, report_name: str) -> Optional[Any]:
    reports = get_run_reports(run_id)
    if reports is None:
        return None
    return reports.get(report_name)


def get_run_decision(run_id: str) -> Optional[Dict[str, Any]]:
    run = get_run(run_id)
    if not run:
        return None

    result = run.get("result")
    if not isinstance(result, dict):
        return None

    decision_block = result.get("decision") if isinstance(result.get("decision"), dict) else {}

    return {
        "run_id": run_id,
        "ticker": result.get("ticker"),
        "analysis_date": result.get("analysis_date"),
        "decision": result.get("decision"),
        "action": result.get("action") or decision_block.get("action"),
        "summary": result.get("summary") or decision_block.get("summary"),
        "confidence": result.get("confidence") or decision_block.get("confidence"),
    }


def cancel_run(run_id: str) -> bool:
    run = get_run(run_id)
    if not run:
        return False

    if run["status"] in {"completed", "failed", "cancelled"}:
        return True

    logs = list(run.get("logs") or [])
    logs.append(
        {
            "ts": _utc_now(),
            "level": "warning",
            "message": "Cancellation requested.",
        }
    )

    return _update_run(
        run_id,
        cancel_requested=True,
        logs=_trim_logs(logs),
    )


def create_or_reuse_run(payload: Dict[str, Any]) -> Dict[str, Any]:
    _init_db()

    normalized_payload = _normalize_payload(payload)
    provider = (
        (normalized_payload.get("params") or {}).get("llm_provider")
        or normalized_payload.get("provider")
        or "openai"
    )
    provider = _normalize_provider(provider)
    validate_provider_env_or_raise(provider)

    payload_hash = _payload_hash(normalized_payload)

    conn = _get_conn()
    try:
        row = conn.execute(
            """
            SELECT * FROM runs
            WHERE payload_hash = ?
            AND status IN ('queued', 'initializing', 'preparing_inputs', 'loading_market_data', 'building_charts', 'importing_engine', 'running')
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (payload_hash,),
        ).fetchone()
    finally:
        conn.close()

    if row:
        existing = _row_to_run(row)
        return {"run_id": existing["run_id"], "reused": True}

    run_id = str(uuid4())
    now = _utc_now()
    caps = _capabilities()

    run = {
        "run_id": run_id,
        "status": "queued",
        "payload": normalized_payload,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
        "error": None,
        "result": None,
        "partial_result": {
            "ticker": None,
            "analysis_date": None,
            "config_ready": False,
            "market_data_ready": False,
            "charts_ready": False,
            "decision_ready": False,
            "market_data_url": None,
            "price_chart_json_url": None,
            "chart_urls": {},
            "result_url": None,
            "summary_cards": {},
            "warnings": [],
            "reports": {},
            "reports_ready": [],
            "artifacts": {},
        },
        "cancel_requested": False,
        "logs": [],
        "poll_after_ms": 1500,
        "capabilities": caps,
        "progress": {
            "stage": "queued",
            "message": "Run queued.",
            "percent": 0,
            "step": 0,
            "total_steps": 7,
        },
        "timings": {
            "started_at": None,
            "last_update_at": now,
            "stage_durations_ms": {},
            "counters": {},
        },
    }

    _insert_run(run, payload_hash)

    worker = threading.Thread(target=_run_in_background, args=(run_id,), daemon=True)
    worker.start()

    return {"run_id": run_id, "reused": False}


def _resolve_ticker(payload: Dict[str, Any]) -> str:
    ticker = payload.get("ticker")
    tickers = payload.get("tickers") or []

    if ticker:
        return str(ticker).strip().upper()

    if tickers:
        return str(tickers[0]).strip().upper()

    raise ValueError("A ticker is required. Provide 'ticker' or at least one item in 'tickers'.")


def _resolve_analysis_date(payload: Dict[str, Any]) -> str:
    if payload.get("analysis_date"):
        return payload["analysis_date"]
    if payload.get("end_date"):
        return payload["end_date"]
    if payload.get("start_date"):
        return payload["start_date"]

    raise ValueError(
        "TradingAgents requires an analysis date. Provide 'analysis_date', 'end_date', or 'start_date' in YYYY-MM-DD format."
    )


def _resolve_range(payload: Dict[str, Any]) -> str:
    value = payload.get("range") or payload.get("window") or "3y"
    text = str(value).strip()

    allowed = {"1mo", "3mo", "6mo", "1y", "2y", "3y", "5y", "10y", "max"}
    return text if text in allowed else "3y"


def _normalize_provider(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    return PROVIDER_ALIASES.get(stripped, stripped.lower())


def _normalize_model(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    return MODEL_ALIASES.get(stripped, stripped)


def _normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = dict(payload)
    params = dict(payload.get("params") or {})

    if "model" in payload:
        payload["model"] = _normalize_model(payload["model"])

    if "provider" in payload:
        payload["provider"] = _normalize_provider(payload["provider"])

    if "llm_provider" in params:
        params["llm_provider"] = _normalize_provider(params["llm_provider"])

    if "deep_think_llm" in params:
        params["deep_think_llm"] = _normalize_model(params["deep_think_llm"])

    if "quick_think_llm" in params:
        params["quick_think_llm"] = _normalize_model(params["quick_think_llm"])

    payload["params"] = params
    return payload


def _build_tradingagents_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = _normalize_payload(payload)

    config_input: Dict[str, Any] = {
        "ticker": _resolve_ticker(payload),
        "analysisDate": _resolve_analysis_date(payload),
        "provider": payload.get("provider")
        or (payload.get("params") or {}).get("llm_provider")
        or "openai",
        "quickModel": (payload.get("params") or {}).get("quick_think_llm")
        or payload.get("model")
        or "",
        "deepModel": (payload.get("params") or {}).get("deep_think_llm")
        or payload.get("model")
        or "",
        "researchDepth": (payload.get("params") or {}).get("research_depth") or "Shallow",
        "language": (payload.get("params") or {}).get("language") or "English",
        "params": dict(payload.get("params") or {}),
    }

    if payload.get("analysts"):
        config_input["analysts"] = payload.get("analysts")
    elif (payload.get("params") or {}).get("analysts"):
        config_input["analysts"] = (payload.get("params") or {}).get("analysts")

    for key in ["strategy", "interval", "initial_capital", "range", "window"]:
        if key in payload:
            config_input["params"][key] = payload[key]

    return normalize_engine_config(config_input)


def _extract_text_block(value: Any) -> str:
    safe = _make_json_safe(value)
    if isinstance(safe, str):
        return safe
    return json.dumps(safe, ensure_ascii=False, indent=2)


def _infer_action_from_text(text: str) -> str:
    lowered = text.lower()
    if "underweight" in lowered:
        return "underweight"
    if "overweight" in lowered:
        return "overweight"
    if "buy" in lowered:
        return "buy"
    if "sell" in lowered:
        return "sell"
    if "hold" in lowered:
        return "hold"
    if "review" in lowered:
        return "review"
    return "unknown"


def _extract_action(decision: Any, state: Any) -> str:
    if isinstance(decision, str) and decision.strip():
        return _infer_action_from_text(decision.strip())

    if isinstance(decision, dict):
        for key in ["action", "rating", "signal"]:
            val = decision.get(key)
            if isinstance(val, str) and val.strip():
                return _infer_action_from_text(val)

    if isinstance(state, dict):
        for key in ["decision", "final_trade_decision", "trader_investment_plan", "investment_plan"]:
            val = state.get(key)
            if isinstance(val, str) and val.strip():
                return _infer_action_from_text(val)

    return _infer_action_from_text(_extract_text_block(decision))


def _extract_summary(decision: Any, state: Any) -> str:
    if isinstance(decision, dict):
        for key in ["executiveSummary", "reasoning", "summary"]:
            val = decision.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()

    if isinstance(state, dict):
        for key in [
            "final_trade_decision",
            "trader_investment_plan",
            "investment_plan",
        ]:
            val = state.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()

        messages = state.get("messages")
        if isinstance(messages, list) and messages:
            last = messages[-1]
            if isinstance(last, dict):
                content = last.get("content")
                if isinstance(content, str) and content.strip():
                    return content.strip()

    if isinstance(decision, str) and decision.strip():
        return decision.strip()

    return _extract_text_block(decision)


def _extract_confidence(state: Any, decision: Any) -> Optional[float]:
    if isinstance(decision, dict):
        direct = decision.get("confidence")
        if isinstance(direct, (int, float)):
            return float(direct)

    if isinstance(state, dict):
        direct = state.get("confidence")
        if isinstance(direct, (int, float)):
            return float(direct)

    return None


def _extract_reports(state: Any) -> Dict[str, Any]:
    if not isinstance(state, dict):
        return {}

    report_keys = [
        "market_report",
        "sentiment_report",
        "news_report",
        "fundamentals_report",
        "investment_plan",
        "trader_investment_plan",
        "final_trade_decision",
        "bull_report",
        "bear_report",
    ]

    reports: Dict[str, Any] = {}
    for key in report_keys:
        if key in state:
            reports[key] = _make_json_safe(state[key])

    return reports


def _normalize_run_tradingagents_output(
    engine_result: Dict[str, Any],
    ticker: str,
    analysis_date: str,
) -> Dict[str, Any]:
    safe_engine_result = _make_json_safe(engine_result)

    decision_structured = (
        safe_engine_result.get("decision_structured")
        if isinstance(safe_engine_result.get("decision_structured"), dict)
        else {}
    )
    raw_result = (
        safe_engine_result.get("raw_result")
        if isinstance(safe_engine_result.get("raw_result"), dict)
        else {}
    )
    final_state = raw_result.get("final_state", safe_engine_result)
    decision_raw = raw_result.get("decision", decision_structured)

    reports = {}
    if isinstance(safe_engine_result.get("reports"), dict):
        reports = _make_json_safe(safe_engine_result["reports"])
    else:
        reports = _extract_reports(final_state)

    action = (
        safe_engine_result.get("action")
        or decision_structured.get("action")
        or _extract_action(decision_raw, final_state)
    )
    summary = (
        safe_engine_result.get("summary")
        or decision_structured.get("executiveSummary")
        or _extract_summary(decision_raw, final_state)
    )
    confidence = (
        safe_engine_result.get("confidence")
        if isinstance(safe_engine_result.get("confidence"), (int, float))
        else decision_structured.get("confidence")
    )
    if not isinstance(confidence, (int, float)):
        confidence = _extract_confidence(final_state, decision_raw)

    result = {
        "ticker": ticker,
        "analysis_date": analysis_date,
        "action": action,
        "summary": summary,
        "confidence": confidence,
        "decision": {
            "action": action,
            "summary": summary,
            "confidence": confidence,
            "raw": _make_json_safe(decision_raw),
            "structured": decision_structured,
        },
        "final_trade_decision": summary,
        "reports": reports,
        "raw_decision": _make_json_safe(decision_raw),
        "state": _make_json_safe(final_state),
        "engine_result": safe_engine_result,
    }

    if isinstance(safe_engine_result.get("engine_error"), str):
        result["engine_error"] = safe_engine_result["engine_error"]

    return result


def _is_cancel_requested(run_id: str) -> bool:
    run = get_run(run_id)
    if not run:
        return False
    return bool(run.get("cancel_requested"))


def _cancel_if_requested(run_id: str, message: str, step: int) -> bool:
    if _is_cancel_requested(run_id):
        _update_run(
            run_id,
            status="cancelled",
            completed_at=_utc_now(),
            progress={
                "stage": "cancelled",
                "message": message,
                "percent": 100,
                "step": step,
                "total_steps": 7,
            },
            poll_after_ms=0,
        )
        _append_log(run_id, message, level="warning")
        return True
    return False


def _safe_round(value: Any) -> Optional[float]:
    if value is None or pd.isna(value):
        return None
    return round(float(value), 4)


def _safe_col_value(df: pd.DataFrame, col: str) -> Optional[float]:
    if col not in df.columns or df.empty:
        return None
    return _safe_round(df[col].iloc[-1])


def _build_summary_cards(ticker: str, analysis_date: str, df: pd.DataFrame) -> Dict[str, Any]:
    if df.empty:
        return {
            "ticker": ticker,
            "analysis_date": analysis_date,
        }

    first_close = _safe_round(df["Close"].iloc[0]) if "Close" in df.columns else None
    last_close = _safe_round(df["Close"].iloc[-1]) if "Close" in df.columns else None
    pct_change = None

    if first_close not in (None, 0) and last_close is not None:
        pct_change = round(((last_close - first_close) / first_close) * 100, 4)

    return {
        "ticker": ticker,
        "analysis_date": analysis_date,
        "stock_name": df["Stock"].iloc[0] if "Stock" in df.columns else ticker,
        "stock_code": df["Stock Code"].iloc[0] if "Stock Code" in df.columns else ticker,
        "latest_close": last_close,
        "latest_ema20": _safe_col_value(df, "EMA20"),
        "latest_ema50": _safe_col_value(df, "EMA50"),
        "latest_rsi14": _safe_col_value(df, "RSI14"),
        "visible_start": df.index.min().strftime("%Y-%m-%d"),
        "visible_end": df.index.max().strftime("%Y-%m-%d"),
        "price_change_percent": pct_change,
        "rows": int(len(df)),
    }


def _artifact_urls(run_id: str) -> Dict[str, str]:
    return {
        "market_data": f"/runs/{run_id}/market-data",
        "market_data_json": f"/runs/{run_id}/market-data.json",
        "price_chart_json": f"/runs/{run_id}/price-chart.json",
        "price_chart_png": f"/runs/{run_id}/price-chart.png",
        "price_chart_html": f"/runs/{run_id}/price-chart.html",
        "chart_png": f"/runs/{run_id}/chart.png",
        "chart_html": f"/runs/{run_id}/chart.html",
        "result_html": f"/runs/{run_id}/result.html",
    }


def _run_in_background(run_id: str) -> None:
    total_start = _now_perf()

    try:
        created_run = get_run(run_id)
        if not created_run:
            return

        created_at_raw = created_run.get("created_at")
        queue_ms = 0
        if created_at_raw:
            try:
                created_dt = datetime.fromisoformat(created_at_raw)
                queue_ms = max(0, int((datetime.now(UTC) - created_dt).total_seconds() * 1000))
            except Exception:
                queue_ms = 0

        _record_timing(run_id, "queue_ms", queue_ms)

        _update_run(
            run_id,
            status="initializing",
            started_at=_utc_now(),
            timings={
                "started_at": _utc_now(),
                "last_update_at": _utc_now(),
            },
            error=None,
        )
        _set_progress(
            run_id,
            status="initializing",
            stage="initialize",
            message="Initializing run.",
            percent=2,
            step=1,
            total_steps=7,
            poll_after_ms=1000,
        )
        _append_log(run_id, "Run started.")
        _append_log(run_id, f"Queue wait: {queue_ms} ms")

        if _cancel_if_requested(run_id, "Run cancelled before execution.", step=1):
            return

        run = get_run(run_id)
        if not run:
            return

        payload = _normalize_payload(run["payload"])
        caps = _capabilities()

        if caps["fred_enabled"]:
            _append_log(run_id, "FRED_API_KEY detected. FRED-backed macro features are available.")
        else:
            _append_log(
                run_id,
                "FRED_API_KEY not configured. FRED-backed macro features may be skipped by the engine.",
                level="warning",
            )

        prepare_start = _now_perf()
        _set_progress(
            run_id,
            status="preparing_inputs",
            stage="preparing_inputs",
            message="Resolving ticker, date, and config.",
            percent=8,
            step=2,
            total_steps=7,
            poll_after_ms=1000,
        )

        ticker = _resolve_ticker(payload)
        analysis_date = _resolve_analysis_date(payload)
        range_str = _resolve_range(payload)
        engine_config = _build_tradingagents_config(payload)

        prepare_ms = _duration_ms(prepare_start)
        _record_timing(run_id, "prepare_inputs_ms", prepare_ms)
        _append_log(
            run_id,
            "Inputs prepared.",
            extra={
                "ticker": ticker,
                "analysis_date": analysis_date,
                "range": range_str,
                "prepare_inputs_ms": prepare_ms,
                "provider": engine_config.get("provider"),
                "researchDepth": engine_config.get("researchDepth"),
            },
        )

        _set_partial_result(
            run_id,
            ticker=ticker,
            analysis_date=analysis_date,
            range=range_str,
            config_ready=True,
            summary_cards={
                "ticker": ticker,
                "analysis_date": analysis_date,
            },
        )

        if _cancel_if_requested(run_id, "Run cancelled after input preparation.", step=2):
            return

        df: Optional[pd.DataFrame] = None

        market_data_start = _now_perf()
        _set_progress(
            run_id,
            status="loading_market_data",
            stage="loading_market_data",
            message=f"Downloading market data for {ticker}.",
            percent=18,
            step=3,
            total_steps=7,
            poll_after_ms=1500,
        )
        _append_log(run_id, f"Downloading market data for {ticker}.")

        try:
            df = download_price_frame(
                ticker=ticker,
                analysis_date=analysis_date,
                range_str=range_str,
            )

            market_data_ms = _duration_ms(market_data_start)
            _record_timing(run_id, "load_market_data_ms", market_data_ms)
            _append_log(
                run_id,
                f"Market data downloaded in {market_data_ms} ms.",
                extra={"rows": len(df)},
            )

            summary_cards = _build_summary_cards(ticker, analysis_date, df)
            urls = _artifact_urls(run_id)

            _set_partial_result(
                run_id,
                market_data_ready=True,
                market_data_url=urls["market_data"],
                price_chart_json_url=urls["price_chart_json"],
                price_data_points=int(len(df)),
                summary_cards=summary_cards,
            )

            try:
                write_market_data_json_artifact(run_id)
                write_price_chart_json_artifact(run_id)
                _append_log(run_id, "Market-data and price-chart JSON artifacts generated.")
            except Exception as artifact_exc:
                warning = f"Market-data artifact generation warning: {artifact_exc}"
                _append_log(run_id, warning, level="warning")
                _add_warning(run_id, warning)

        except Exception as market_exc:
            market_data_ms = _duration_ms(market_data_start)
            _record_timing(run_id, "load_market_data_ms", market_data_ms)
            warning = f"Market data unavailable: {market_exc}"
            _append_log(run_id, warning, level="warning")
            _add_warning(run_id, warning)
            _set_partial_result(
                run_id,
                market_data_ready=False,
                market_data_url=None,
                price_chart_json_url=None,
            )

        if _cancel_if_requested(run_id, "Run cancelled after market data stage.", step=3):
            return

        _set_progress(
            run_id,
            status="building_charts",
            stage="building_charts",
            message="Building chart artifacts.",
            percent=35,
            step=4,
            total_steps=7,
            poll_after_ms=1500,
        )
        _append_log(run_id, "Building price chart artifacts.")

        if df is not None and not df.empty:
            charts_start = _now_perf()
            try:
                write_price_chart_png_artifact(run_id)
                write_price_chart_html_artifact(run_id)

                chart_urls = {
                    "png": f"/runs/{run_id}/price-chart.png",
                    "html": f"/runs/{run_id}/price-chart.html",
                    "png_legacy": f"/runs/{run_id}/chart.png",
                    "html_legacy": f"/runs/{run_id}/chart.html",
                }

                charts_ms = _duration_ms(charts_start)
                _record_timing(run_id, "build_charts_ms", charts_ms)
                _append_log(run_id, f"Price chart artifacts built in {charts_ms} ms.", extra=chart_urls)

                _set_partial_result(
                    run_id,
                    charts_ready=True,
                    chart_urls=chart_urls,
                )
            except Exception as chart_exc:
                charts_ms = _duration_ms(charts_start)
                _record_timing(run_id, "build_charts_ms", charts_ms)
                warning = f"Chart artifacts unavailable: {chart_exc}"
                _append_log(run_id, warning, level="warning")
                _add_warning(run_id, warning)
                _set_partial_result(
                    run_id,
                    charts_ready=False,
                    chart_urls={},
                )
        else:
            warning = "Chart artifacts skipped because market data was unavailable."
            _append_log(run_id, warning, level="warning")
            _add_warning(run_id, warning)
            _set_partial_result(
                run_id,
                charts_ready=False,
                chart_urls={},
            )

        if _cancel_if_requested(run_id, "Run cancelled before engine import.", step=4):
            return

        import_start = _now_perf()
        _set_progress(
            run_id,
            status="importing_engine",
            stage="importing_engine",
            message="Preparing TradingAgents engine adapter.",
            percent=45,
            step=5,
            total_steps=7,
            poll_after_ms=2000,
        )
        _append_log(run_id, "Preparing TradingAgents engine adapter.")

        if not engine_available():
            raise RuntimeError("TradingAgents engine is unavailable.")

        import_ms = _duration_ms(import_start)
        _record_timing(run_id, "import_engine_ms", import_ms)
        _append_log(run_id, f"TradingAgents engine adapter ready in {import_ms} ms.")

        if _cancel_if_requested(run_id, "Run cancelled before AI analysis.", step=5):
            return

        propagate_start = _now_perf()
        _set_progress(
            run_id,
            status="running",
            stage="running_ai_analysis",
            message=f"Running AI analysis for {ticker} on {analysis_date}.",
            percent=60,
            step=6,
            total_steps=7,
            poll_after_ms=5000,
        )
        _append_log(run_id, "Calling TradingAgents engine adapter.")
        _increment_counter(run_id, "engine_calls_count", 1)

        engine_result = run_tradingagents(engine_config)

        propagate_ms = _duration_ms(propagate_start)
        _record_timing(run_id, "propagate_ms", propagate_ms)
        _append_log(run_id, f"TradingAgents engine adapter completed in {propagate_ms} ms.")

        if isinstance(engine_result.get("engine_error"), str):
            warning = f"TradingAgents engine fallback used: {engine_result['engine_error']}"
            _append_log(run_id, warning, level="warning")
            _add_warning(run_id, warning)

        if _cancel_if_requested(run_id, "Run finished engine call but was marked cancelled before save.", step=6):
            return

        build_result_start = _now_perf()
        _set_progress(
            run_id,
            status="running",
            stage="build_result",
            message="Building result payload.",
            percent=90,
            step=7,
            total_steps=7,
            poll_after_ms=1500,
        )

        result = _normalize_run_tradingagents_output(
            engine_result=engine_result,
            ticker=ticker,
            analysis_date=analysis_date,
        )

        build_result_ms = _duration_ms(build_result_start)
        _record_timing(run_id, "build_result_ms", build_result_ms)
        _append_log(run_id, f"Result payload built in {build_result_ms} ms.")

        urls = _artifact_urls(run_id)

        _update_run(
            run_id,
            status="completed",
            completed_at=_utc_now(),
            result=result,
            error=None,
            partial_result={
                **(get_run(run_id).get("partial_result") or {}),
                "decision_ready": True,
                "action": result.get("action"),
                "summary": result.get("summary"),
                "confidence": result.get("confidence"),
                "reports": result.get("reports"),
                "reports_ready": list((result.get("reports") or {}).keys()),
                "result_url": urls["result_html"],
                "artifacts": urls,
            },
            progress={
                "stage": "completed",
                "message": f"Run completed for {ticker}.",
                "percent": 100,
                "step": 7,
                "total_steps": 7,
            },
            poll_after_ms=0,
        )

        artifact_start = _now_perf()
        try:
            write_all_core_artifacts(run_id)
            artifact_ms = _duration_ms(artifact_start)
            _record_timing(run_id, "artifact_save_ms", artifact_ms)
            _append_log(run_id, f"All core artifacts saved in {artifact_ms} ms.")
        except Exception as artifact_exc:
            artifact_ms = _duration_ms(artifact_start)
            _record_timing(run_id, "artifact_save_ms", artifact_ms)
            warning = f"Artifact generation warning after completion: {artifact_exc}"
            _append_log(run_id, warning, level="warning")
            _add_warning(run_id, warning)
            try:
                write_result_html_artifact(run_id)
            except Exception:
                pass

        total_ms = _duration_ms(total_start)
        _record_timing(run_id, "total_ms", total_ms)
        _append_log(run_id, f"TradingAgents run completed successfully in {total_ms} ms.")

    except Exception as exc:
        tb = traceback.format_exc()
        total_ms = _duration_ms(total_start)
        _record_timing(run_id, "total_ms", total_ms)
        _update_run(
            run_id,
            status="failed",
            completed_at=_utc_now(),
            error={
                "message": str(exc),
                "traceback": tb,
            },
            progress={
                "stage": "failed",
                "message": f"Run failed: {exc}",
                "percent": 100,
                "step": 7,
                "total_steps": 7,
            },
            poll_after_ms=0,
        )
        _append_log(run_id, f"Run failed after {total_ms} ms: {exc}", level="error")


def _make_json_safe(value: Any) -> Any:
    try:
        json.dumps(value)
        return value
    except TypeError:
        pass

    if isinstance(value, dict):
        return {str(k): _make_json_safe(v) for k, v in value.items()}

    if isinstance(value, (list, tuple, set)):
        return [_make_json_safe(v) for v in value]

    if hasattr(value, "model_dump"):
        try:
            return _make_json_safe(value.model_dump())
        except Exception:
            pass

    if hasattr(value, "dict"):
        try:
            return _make_json_safe(value.dict())
        except Exception:
            pass

    if hasattr(value, "__dict__"):
        try:
            return _make_json_safe(vars(value))
        except Exception:
            pass

    return str(value)

def regenerate_run_artifacts(run_id: str) -> Dict[str, Any]:
    run = get_run(run_id)
    if not run:
        raise ValueError("Run not found.")

    write_all_core_artifacts(run_id)

    return {
        "message": "Artifacts regenerated successfully.",
        "artifacts": _artifact_urls(run_id),
    }

_init_db()