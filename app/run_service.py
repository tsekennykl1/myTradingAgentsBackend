import json
import os
import threading
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import uuid4

import pandas as pd

from app.artifacts import (
    save_market_data_json_content,
    save_price_chart_html_content,
    save_price_chart_json_content,
    save_price_chart_png_bytes,
    save_result_html_content,
)
from app.chart_payloads import build_price_chart_payload
from app.charts import build_charts
from app.market_data import df_to_price_payload, download_price_frame


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RUNS_FILE = DATA_DIR / "runs.json"
TEMP_CHARTS_DIR = DATA_DIR / "tmp_charts"

_LOCK = threading.Lock()


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


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _now_perf() -> float:
    return time.perf_counter()


def _duration_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _ensure_storage() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    TEMP_CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    if not RUNS_FILE.exists():
        RUNS_FILE.write_text("{}", encoding="utf-8")


def _load_runs_unlocked() -> Dict[str, Dict[str, Any]]:
    _ensure_storage()
    try:
        return json.loads(RUNS_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _load_runs() -> Dict[str, Dict[str, Any]]:
    with _LOCK:
        return _load_runs_unlocked()


def _save_runs_unlocked(runs: Dict[str, Dict[str, Any]]) -> None:
    _ensure_storage()
    RUNS_FILE.write_text(json.dumps(runs, indent=2, ensure_ascii=False), encoding="utf-8")


def _deep_merge_dict(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge_dict(out[key], value)
        else:
            out[key] = value
    return out


def _capabilities() -> Dict[str, Any]:
    return {
        "fred_enabled": bool(os.getenv("FRED_API_KEY")),
    }


def _trim_logs(logs: list[Dict[str, Any]], max_entries: int = 200) -> list[Dict[str, Any]]:
    if len(logs) <= max_entries:
        return logs
    return logs[-max_entries:]


def _update_run(run_id: str, **updates: Any) -> bool:
    with _LOCK:
        runs = _load_runs_unlocked()
        run = runs.get(run_id)
        if not run:
            return False

        merged = _deep_merge_dict(run, updates)
        merged["updated_at"] = _utc_now()

        if isinstance(merged.get("logs"), list):
            merged["logs"] = _trim_logs(merged["logs"])

        runs[run_id] = merged
        _save_runs_unlocked(runs)
        return True


def _append_log(run_id: str, message: str, level: str = "info", extra: Optional[Dict[str, Any]] = None) -> None:
    with _LOCK:
        runs = _load_runs_unlocked()
        run = runs.get(run_id)
        if not run:
            return

        logs = run.setdefault("logs", [])
        entry: Dict[str, Any] = {
            "ts": _utc_now(),
            "level": level,
            "message": message,
        }
        if extra:
            entry["extra"] = extra
        logs.append(entry)
        run["logs"] = _trim_logs(logs)
        run["updated_at"] = _utc_now()
        runs[run_id] = run
        _save_runs_unlocked(runs)


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

    with _LOCK:
        runs = _load_runs_unlocked()
        changed = False

        for run_id, run in runs.items():
            if run.get("status") in recoverable_statuses and not run.get("completed_at"):
                run["status"] = "failed"
                run["completed_at"] = _utc_now()
                run["updated_at"] = _utc_now()
                run["error"] = {
                    "message": "Run was interrupted by a server restart or worker shutdown before completion.",
                    "code": "orphaned_run",
                }
                logs = run.setdefault("logs", [])
                logs.append(
                    {
                        "ts": _utc_now(),
                        "level": "warning",
                        "message": "Run marked failed during startup reconciliation.",
                    }
                )
                run["logs"] = _trim_logs(logs)
                runs[run_id] = run
                changed = True

        if changed:
            _save_runs_unlocked(runs)


def engine_available() -> bool:
    try:
        from tradingagents.graph.trading_graph import TradingAgentsGraph  # noqa: F401
        from tradingagents.default_config import DEFAULT_CONFIG  # noqa: F401
        return True
    except Exception:
        return False


def create_run(payload: Dict[str, Any]) -> str:
    run_id = str(uuid4())
    now = _utc_now()

    normalized_payload = _normalize_payload(payload)
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

    with _LOCK:
        runs = _load_runs_unlocked()
        runs[run_id] = run
        _save_runs_unlocked(runs)

    worker = threading.Thread(target=_run_in_background, args=(run_id,), daemon=True)
    worker.start()

    return run_id


def list_runs() -> list[Dict[str, Any]]:
    runs = _load_runs()
    return list(runs.values())


def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    runs = _load_runs()
    return runs.get(run_id)


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

    return {
        "run_id": run_id,
        "ticker": result.get("ticker"),
        "analysis_date": result.get("analysis_date"),
        "decision": result.get("decision"),
        "action": result.get("action"),
        "summary": result.get("summary"),
        "confidence": result.get("confidence"),
    }


def cancel_run(run_id: str) -> bool:
    with _LOCK:
        runs = _load_runs_unlocked()
        run = runs.get(run_id)
        if not run:
            return False

        if run["status"] in {"completed", "failed", "cancelled"}:
            return True

        run["cancel_requested"] = True
        run["updated_at"] = _utc_now()
        logs = run.setdefault("logs", [])
        logs.append(
            {
                "ts": _utc_now(),
                "level": "warning",
                "message": "Cancellation requested.",
            }
        )
        run["logs"] = _trim_logs(logs)
        runs[run_id] = run
        _save_runs_unlocked(runs)
        return True


def _resolve_ticker(payload: Dict[str, Any]) -> str:
    ticker = payload.get("ticker")
    tickers = payload.get("tickers") or []

    if ticker:
        return str(ticker).strip().upper()

    if tickers:
        return str(tickers[0]).strip().upper()

    raise ValueError("A ticker is required. Provide 'ticker' or at least one item in 'tickers'.")


def _resolve_analysis_date(payload: Dict[str, Any]) -> str:
    if payload.get("end_date"):
        return payload["end_date"]
    if payload.get("start_date"):
        return payload["start_date"]

    raise ValueError(
        "TradingAgents requires an analysis date. Provide 'end_date' or 'start_date' in YYYY-MM-DD format."
    )


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

    if "llm_provider" in params:
        params["llm_provider"] = _normalize_provider(params["llm_provider"])

    if "deep_think_llm" in params:
        params["deep_think_llm"] = _normalize_model(params["deep_think_llm"])

    if "quick_think_llm" in params:
        params["quick_think_llm"] = _normalize_model(params["quick_think_llm"])

    payload["params"] = params
    return payload


def _build_tradingagents_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    from tradingagents.default_config import DEFAULT_CONFIG

    payload = _normalize_payload(payload)
    config = DEFAULT_CONFIG.copy()
    params = payload.get("params") or {}

    model = payload.get("model")
    strategy = payload.get("strategy")
    interval = payload.get("interval")
    initial_capital = payload.get("initial_capital")

    if model and "deep_think_llm" not in params:
        config["deep_think_llm"] = model

    if model and "quick_think_llm" not in params:
        config["quick_think_llm"] = model

    if strategy is not None:
        config["strategy"] = strategy

    if interval is not None:
        config["interval"] = interval

    if initial_capital is not None:
        config["initial_capital"] = initial_capital

    for key, value in params.items():
        if key == "llm_provider":
            config[key] = _normalize_provider(value)
        elif key in {"deep_think_llm", "quick_think_llm"}:
            config[key] = _normalize_model(value)
        else:
            config[key] = value

    return config


def _extract_text_block(value: Any) -> str:
    safe = _make_json_safe(value)
    if isinstance(safe, str):
        return safe
    return json.dumps(safe, ensure_ascii=False, indent=2)


def _extract_action(decision: Any, state: Any) -> str:
    if isinstance(decision, str) and decision.strip():
        return _infer_action_from_text(decision.strip())

    if isinstance(state, dict):
        for key in ["decision", "final_trade_decision", "trader_investment_plan", "investment_plan"]:
            val = state.get(key)
            if isinstance(val, str) and val.strip():
                return _infer_action_from_text(val)

    return _infer_action_from_text(_extract_text_block(decision))


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


def _extract_summary(decision: Any, state: Any) -> str:
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


def _extract_confidence(state: Any) -> Optional[float]:
    if not isinstance(state, dict):
        return None

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
    ]

    reports: Dict[str, Any] = {}
    for key in report_keys:
        if key in state:
            reports[key] = _make_json_safe(state[key])

    return reports


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


def _build_summary_cards(ticker: str, analysis_date: str, df: pd.DataFrame) -> Dict[str, Any]:
    if df.empty:
        return {
            "ticker": ticker,
            "analysis_date": analysis_date,
        }

    first_close = _safe_round(df["Close"].iloc[0])
    last_close = _safe_round(df["Close"].iloc[-1])
    pct_change = None

    if first_close not in (None, 0) and last_close is not None:
        pct_change = round(((last_close - first_close) / first_close) * 100, 4)

    return {
        "ticker": ticker,
        "analysis_date": analysis_date,
        "stock_name": df["Stock"].iloc[0] if "Stock" in df.columns else ticker,
        "stock_code": df["Stock Code"].iloc[0] if "Stock Code" in df.columns else ticker,
        "latest_close": last_close,
        "latest_ema20": _safe_round(df["EMA20"].iloc[-1]),
        "latest_ema50": _safe_round(df["EMA50"].iloc[-1]),
        "latest_rsi14": _safe_round(df["RSI14"].iloc[-1]),
        "visible_start": df.index.min().strftime("%Y-%m-%d"),
        "visible_end": df.index.max().strftime("%Y-%m-%d"),
        "price_change_percent": pct_change,
        "rows": int(len(df)),
    }


def _build_and_store_price_charts_for_run(run_id: str, ticker: str, df: pd.DataFrame) -> Dict[str, str]:
    run_dir = TEMP_CHARTS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    build_charts(run_dir, run_id, ticker, df)

    html_path = run_dir / "chart.html"
    png_path = run_dir / "chart.png"

    if html_path.exists():
        save_price_chart_html_content(run_id, html_path.read_text(encoding="utf-8"))

    if png_path.exists():
        save_price_chart_png_bytes(run_id, png_path.read_bytes())

    return {
        "plotly": f"/runs/{run_id}/price-chart.html",
        "png": f"/runs/{run_id}/price-chart.png",
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
        config = _build_tradingagents_config(payload)

        prepare_ms = _duration_ms(prepare_start)
        _record_timing(run_id, "prepare_inputs_ms", prepare_ms)
        _append_log(
            run_id,
            "Inputs prepared.",
            extra={
                "ticker": ticker,
                "analysis_date": analysis_date,
                "prepare_inputs_ms": prepare_ms,
            },
        )

        _set_partial_result(
            run_id,
            ticker=ticker,
            analysis_date=analysis_date,
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
            df = download_price_frame(ticker, analysis_date)

            price_payload = df_to_price_payload(df)
            save_market_data_json_content(
                run_id,
                json.dumps(
                    {
                        "ticker": ticker,
                        "analysis_date": analysis_date,
                        "rows": price_payload,
                    },
                    ensure_ascii=False,
                ),
            )

            chart_payload = build_price_chart_payload(ticker, analysis_date, df)
            save_price_chart_json_content(
                run_id,
                json.dumps(chart_payload, ensure_ascii=False),
            )

            market_data_ms = _duration_ms(market_data_start)
            _record_timing(run_id, "load_market_data_ms", market_data_ms)
            _append_log(
                run_id,
                f"Market data downloaded in {market_data_ms} ms.",
                extra={"rows": len(price_payload)},
            )

            summary_cards = _build_summary_cards(ticker, analysis_date, df)

            _set_partial_result(
                run_id,
                market_data_ready=True,
                market_data_url=f"/runs/{run_id}/market-data",
                price_chart_json_url=f"/runs/{run_id}/price-chart.json",
                price_data_points=len(price_payload),
                summary_cards=summary_cards,
            )

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
                chart_urls = _build_and_store_price_charts_for_run(run_id, ticker, df)
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
            message="Importing TradingAgents engine.",
            percent=45,
            step=5,
            total_steps=7,
            poll_after_ms=2000,
        )
        _append_log(run_id, "Importing TradingAgents.")
        from tradingagents.graph.trading_graph import TradingAgentsGraph
        from tradingagents.default_config import DEFAULT_CONFIG  # noqa: F401

        import_ms = _duration_ms(import_start)
        _record_timing(run_id, "import_engine_ms", import_ms)
        _append_log(run_id, f"TradingAgents imported in {import_ms} ms.")

        if _cancel_if_requested(run_id, "Run cancelled before graph construction.", step=5):
            return

        construct_start = _now_perf()
        _set_progress(
            run_id,
            status="running",
            stage="construct_graph",
            message=f"Constructing TradingAgentsGraph for {ticker}.",
            percent=52,
            step=6,
            total_steps=7,
            poll_after_ms=2500,
        )
        _append_log(run_id, "Constructing TradingAgentsGraph.")

        ta = TradingAgentsGraph(
            debug=True,
            config=config,
        )

        construct_ms = _duration_ms(construct_start)
        _record_timing(run_id, "construct_graph_ms", construct_ms)
        _append_log(run_id, f"TradingAgentsGraph constructed in {construct_ms} ms.")

        if _cancel_if_requested(run_id, "Run cancelled before AI analysis.", step=6):
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
        _append_log(run_id, "Calling TradingAgentsGraph.propagate().")
        _increment_counter(run_id, "engine_calls_count", 1)

        state, decision = ta.propagate(ticker, analysis_date)

        propagate_ms = _duration_ms(propagate_start)
        _record_timing(run_id, "propagate_ms", propagate_ms)
        _append_log(run_id, f"TradingAgentsGraph.propagate() completed in {propagate_ms} ms.")

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

        safe_state = _make_json_safe(state)
        safe_decision = _make_json_safe(decision)

        action = _extract_action(safe_decision, safe_state)
        summary = _extract_summary(safe_decision, safe_state)
        confidence = _extract_confidence(safe_state)
        reports = _extract_reports(safe_state)

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
                "raw": safe_decision,
            },
            "final_trade_decision": summary,
            "reports": reports,
            "raw_decision": safe_decision,
            "state": safe_state,
        }

        build_result_ms = _duration_ms(build_result_start)
        _record_timing(run_id, "build_result_ms", build_result_ms)
        _append_log(run_id, f"Result payload built in {build_result_ms} ms.")

        _set_partial_result(
            run_id,
            decision_ready=True,
            action=action,
            summary=summary,
            confidence=confidence,
            reports=reports,
            reports_ready=list(reports.keys()),
            result_url=f"/runs/{run_id}/result.html",
        )

        artifact_start = _now_perf()
        html = _build_result_html(run_id, ticker, analysis_date, result["decision"])
        save_result_html_content(run_id, html)
        artifact_ms = _duration_ms(artifact_start)
        _record_timing(run_id, "artifact_save_ms", artifact_ms)
        _append_log(run_id, f"Result artifact saved in {artifact_ms} ms.")

        total_ms = _duration_ms(total_start)
        _record_timing(run_id, "total_ms", total_ms)

        _update_run(
            run_id,
            status="completed",
            completed_at=_utc_now(),
            result=result,
            error=None,
            progress={
                "stage": "completed",
                "message": f"Run completed for {ticker}.",
                "percent": 100,
                "step": 7,
                "total_steps": 7,
            },
            poll_after_ms=0,
        )
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


def _build_result_html(run_id: str, ticker: str, analysis_date: str, decision: Any) -> str:
    decision_text = _escape_html(json.dumps(_make_json_safe(decision), indent=2, ensure_ascii=False))

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TradingAgents Result - {ticker}</title>
  <style>
    body {{
      margin: 0;
      padding: 24px;
      background: #0f172a;
      color: #e2e8f0;
      font-family: Arial, sans-serif;
    }}
    .card {{
      max-width: 1000px;
      margin: 0 auto;
      background: #111827;
      border-radius: 12px;
      padding: 24px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.35);
    }}
    h1 {{
      margin-top: 0;
      font-size: 28px;
    }}
    .meta {{
      color: #94a3b8;
      margin-bottom: 20px;
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #020617;
      padding: 16px;
      border-radius: 8px;
      overflow-x: auto;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>TradingAgents Result</h1>
    <div class="meta">
      <div><strong>Run ID:</strong> {run_id}</div>
      <div><strong>Ticker:</strong> {ticker}</div>
      <div><strong>Analysis date:</strong> {analysis_date}</div>
    </div>
    <h2>Decision</h2>
    <pre>{decision_text}</pre>
  </div>
</body>
</html>
"""


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")