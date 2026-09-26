"""Artifacts: the files a finished (or in-progress) run leaves on disk.

One folder per run under ARTIFACTS_DIR, containing:
  market-data.json   full OHLCV history plus computed indicators
  price-chart.json   compact chart payload the React chart reads
  price-chart.png    static image of the chart (matplotlib)
  price-chart.html   interactive Plotly chart
  result.html        printable report page for the whole run

Three families of functions:
  build_*   compute the content in memory (no disk write)
  write_*   compute and save it to the run folder
  get_*     read it back, rebuilding on demand if the file is missing

The HTTP endpoints in app/routes/runs.py only ever call the get_* functions, so
a browser request can never fail just because a file was cleaned up.
"""

from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Any, Optional

from app.market_data import df_to_price_payload, download_price_frame


ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", "artifacts"))


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _run_dir(run_id: str) -> Path:
    path = ARTIFACTS_DIR / run_id
    _ensure_dir(path)
    return path


def _artifact_path(run_id: str, filename: str) -> Path:
    return _run_dir(run_id) / filename


def _safe_read_bytes(path: Path) -> Optional[bytes]:
    try:
        if not path.exists():
            return None
        return path.read_bytes()
    except Exception:
        return None


def _safe_read_text(path: Path) -> Optional[str]:
    try:
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _safe_write_bytes(path: Path, content: bytes) -> None:
    try:
        _ensure_dir(path.parent)
        path.write_bytes(content)
    except Exception:
        pass


def _safe_write_text(path: Path, content: str) -> None:
    try:
        _ensure_dir(path.parent)
        path.write_text(content, encoding="utf-8")
    except Exception:
        pass


def _json_bytes(payload: dict[str, Any], *, indent: int = 2) -> bytes:
    return json.dumps(payload, ensure_ascii=False, indent=indent).encode("utf-8")


def _json_text(payload: dict[str, Any], *, indent: int = 2) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=indent)


def _escape_html(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
    )


def _resolve_run(run_id: str) -> Optional[dict[str, Any]]:
    try:
        from app.services.analyze_service import get_run
    except Exception:
        return None

    try:
        run = get_run(run_id)
        if run is None:
            return None
        if not isinstance(run, dict):
            return None
        return run
    except Exception:
        return None


def _resolve_run_symbol(run: dict[str, Any]) -> Optional[str]:
    payload = run.get("payload") or {}
    partial = run.get("partial_result") or {}
    result = run.get("result") or {}

    candidates = [
        payload.get("ticker"),
        payload.get("symbol"),
        payload.get("stock_code"),
        partial.get("ticker"),
        partial.get("symbol"),
        result.get("ticker"),
        result.get("symbol"),
    ]

    for value in candidates:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text

    return None


def _resolve_run_analysis_date(run: dict[str, Any]) -> Optional[str]:
    payload = run.get("payload") or {}
    partial = run.get("partial_result") or {}
    result = run.get("result") or {}

    candidates = [
        partial.get("analysis_date"),
        result.get("analysis_date"),
        payload.get("analysis_date"),
        payload.get("end_date"),
        payload.get("as_of_date"),
        payload.get("date"),
        payload.get("start_date"),
    ]

    for value in candidates:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text

    return None


def _resolve_run_range(run: dict[str, Any]) -> str:
    payload = run.get("payload") or {}
    partial = run.get("partial_result") or {}
    result = run.get("result") or {}

    value = (
        payload.get("range")
        or partial.get("range")
        or result.get("range")
        or payload.get("window")
        or "3y"
    )
    text = str(value).strip()

    allowed = {"1mo", "3mo", "6mo", "1y", "2y", "3y", "5y", "10y", "max"}
    return text if text in allowed else "3y"


def _resolve_run_name_from_df(df) -> Optional[str]:
    try:
        if "Stock" in df.columns and not df.empty:
            value = df["Stock"].iloc[-1]
            if value is not None:
                text = str(value).strip()
                if text:
                    return text
    except Exception:
        return None
    return None


def _resolve_run_code_from_df(df) -> Optional[str]:
    try:
        if "Stock Code" in df.columns and not df.empty:
            value = df["Stock Code"].iloc[-1]
            if value is not None:
                text = str(value).strip()
                if text:
                    return text
    except Exception:
        return None
    return None


def _download_run_price_df(run_id: str):
    run = _resolve_run(run_id)
    if not run:
        raise ValueError(f"Run not found: {run_id}")

    ticker = _resolve_run_symbol(run)
    if not ticker:
        raise ValueError(f"Run {run_id} does not contain a ticker/symbol")

    analysis_date = _resolve_run_analysis_date(run)
    if not analysis_date:
        raise ValueError(f"Run {run_id} does not contain an analysis date")

    range_str = _resolve_run_range(run)

    df = download_price_frame(
        ticker=ticker,
        analysis_date=analysis_date,
        range_str=range_str,
    )

    return run, ticker, analysis_date, range_str, df


def build_price_chart_json_payload(run_id: str) -> dict[str, Any]:
    run, ticker, analysis_date, range_str, df = _download_run_price_df(run_id)

    rows = df_to_price_payload(df)
    stock_name = _resolve_run_name_from_df(df) or ticker
    stock_code = _resolve_run_code_from_df(df) or ticker

    return {
        "run_id": run_id,
        "ticker": ticker,
        "analysis_date": analysis_date,
        "meta": {
            "stock_name": stock_name,
            "stock_code": stock_code,
            "visible_start": rows[0]["date"] if rows else None,
            "visible_end": rows[-1]["date"] if rows else None,
            "rows": len(rows),
            "range": range_str,
            "vwap_mode": "rolling_vwap_20",
        },
        "rows": rows,
    }


def build_market_data_json_payload(run_id: str) -> dict[str, Any]:
    run = _resolve_run(run_id)
    if not run:
        raise ValueError(f"Run not found: {run_id}")

    payload = run.get("payload") or {}
    partial = run.get("partial_result") or {}
    result = run.get("result") or {}

    ticker = _resolve_run_symbol(run)
    analysis_date = _resolve_run_analysis_date(run)
    range_str = _resolve_run_range(run)

    rows: list[dict[str, Any]] = []
    stock_name = ticker
    stock_code = ticker
    visible_start = None
    visible_end = None

    if ticker and analysis_date:
        try:
            _, _, _, _, df = _download_run_price_df(run_id)
            rows = df_to_price_payload(df)
            stock_name = _resolve_run_name_from_df(df) or ticker
            stock_code = _resolve_run_code_from_df(df) or ticker
            visible_start = rows[0]["date"] if rows else None
            visible_end = rows[-1]["date"] if rows else None
        except Exception:
            rows = []

    result_summary: dict[str, Any] = {}
    if isinstance(result, dict):
        result_summary = {
            "action": result.get("action"),
            "confidence": result.get("confidence"),
            "analysis_date": result.get("analysis_date"),
            "summary": result.get("summary"),
        }

    return {
        "run_id": run_id,
        "ticker": ticker,
        "analysis_date": analysis_date,
        "range": range_str,
        "meta": {
            "stock_name": stock_name,
            "stock_code": stock_code,
            "visible_start": visible_start,
            "visible_end": visible_end,
            "rows": len(rows),
            "vwap_mode": "rolling_vwap_20",
        },
        "payload": payload,
        "partial_result": partial,
        "result_summary": result_summary,
        "rows": rows,
    }


def _render_price_chart_html(run_id: str) -> str:
    payload = build_price_chart_json_payload(run_id)
    try:
        from app.charts import render_price_chart_html
    except Exception as exc:
        raise RuntimeError(f"app.charts.render_price_chart_html is unavailable: {exc}") from exc

    return render_price_chart_html(payload)


def _render_price_chart_png(run_id: str) -> bytes:
    payload = build_price_chart_json_payload(run_id)
    try:
        from app.charts import render_price_chart_png
    except Exception as exc:
        raise RuntimeError(f"app.charts.render_price_chart_png is unavailable: {exc}") from exc

    content = render_price_chart_png(payload)
    if not isinstance(content, (bytes, bytearray)) or not content:
        raise RuntimeError("render_price_chart_png returned empty or invalid bytes")
    return bytes(content)


# ---------------------------------------------------------------------------
# ★ NEW — helpers for the improved result.html (Option A)
# ---------------------------------------------------------------------------

def _public_base_url() -> str:
    """Return PUBLIC_BASE_URL so generated HTML links hit the API, not the SPA."""
    return os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")


def _absolute_url(path: str) -> str:
    """Turn a relative API path into an absolute URL when PUBLIC_BASE_URL is set."""
    base = _public_base_url()
    return f"{base}{path}" if base else path


def _embed_chart_png_as_base64(run_id: str) -> Optional[str]:
    """Read the price chart PNG for this run and return a data: URI, or None."""
    png_path = _artifact_path(run_id, "price-chart.png")
    content = _safe_read_bytes(png_path)
    if not content:
        # Try the legacy filename
        content = _safe_read_bytes(_artifact_path(run_id, "chart.png"))
    if not content:
        # Try rendering on the fly (will fail gracefully if matplotlib unavailable)
        try:
            content = _render_price_chart_png(run_id)
        except Exception:
            return None
    if not content:
        return None
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:image/png;base64,{encoded}"


_REPORT_DISPLAY_NAMES: dict[str, str] = {
    "market_report": "📊 Market Analyst",
    "sentiment_report": "💬 Sentiment Analyst",
    "news_report": "📰 News Analyst",
    "fundamentals_report": "📈 Fundamentals Analyst",
    "investment_plan": "📋 Investment Plan",
    "trader_investment_plan": "🎯 Trader Investment Plan",
    "trader_plan": "🎯 Trader Plan",
    "bull_report": "🐂 Bull Researcher",
    "bear_report": "🐻 Bear Researcher",
    "risk_aggressive": "⚡ Risk — Aggressive",
    "risk_conservative": "🛡️ Risk — Conservative",
    "risk_neutral": "⚖️ Risk — Neutral",
    "final_trade_decision": "✅ Final Trade Decision",
    "final_decision": "✅ Final Decision",
}


def _report_display_name(key: str) -> str:
    if key in _REPORT_DISPLAY_NAMES:
        return _REPORT_DISPLAY_NAMES[key]
    # Title-case the key: "market_report" → "Market Report"
    return key.replace("_", " ").title()


def _format_report_text(value: Any) -> str:
    """Turn a report value (str, dict, or anything) into escaped HTML text."""
    if isinstance(value, str):
        return _escape_html(value)
    if isinstance(value, dict):
        return _escape_html(json.dumps(value, ensure_ascii=False, indent=2))
    return _escape_html(str(value))


def _build_reports_html(reports: dict[str, Any]) -> str:
    """Render all reports as collapsible <details> sections."""
    if not reports:
        return '<p class="muted">No agent reports available for this run.</p>'

    # Preferred display order
    order = [
        "market_report", "sentiment_report", "news_report", "fundamentals_report",
        "bull_report", "bear_report", "investment_plan", "trader_investment_plan",
        "trader_plan", "risk_aggressive", "risk_conservative", "risk_neutral",
        "final_trade_decision", "final_decision",
    ]
    ordered_keys = [k for k in order if k in reports]
    remaining = [k for k in reports if k not in ordered_keys]
    all_keys = ordered_keys + sorted(remaining)

    parts: list[str] = []
    for key in all_keys:
        content = reports[key]
        if content is None or (isinstance(content, str) and not content.strip()):
            continue
        display = _report_display_name(key)
        body = _format_report_text(content)
        parts.append(
            f'<details class="report-section" open>\n'
            f"  <summary>{display}</summary>\n"
            f'  <div class="report-body">{body}</div>\n'
            f"</details>"
        )

    return "\n".join(parts) if parts else '<p class="muted">No agent reports available.</p>'


def _action_color(action: str) -> str:
    """CSS colour for the action badge."""
    lowered = action.strip().lower()
    if lowered in ("buy", "overweight"):
        return "#16a34a"
    if lowered in ("sell", "underweight"):
        return "#dc2626"
    if lowered in ("hold", "review"):
        return "#d97706"
    return "#64748b"


def build_result_html_content(run_id: str) -> str:
    """Build a rich, self-contained HTML report for one completed run.

    The page is fully standalone: inline CSS, no JavaScript frameworks, and the
    price chart is embedded as a base64 PNG data-URI so the file works even when
    saved to disk, printed, or emailed.
    """
    run = _resolve_run(run_id)
    if not run:
        raise ValueError(f"Run not found: {run_id}")

    payload = run.get("payload") or {}
    partial = run.get("partial_result") or {}
    result = run.get("result") or {}

    # ── Scalar fields ──────────────────────────────────────────────────────
    run_id_safe = _escape_html(run_id)
    status = _escape_html(str(run.get("status", "unknown")))
    ticker = _resolve_run_symbol(run) or "n/a"
    ticker_safe = _escape_html(ticker)
    analysis_date = _resolve_run_analysis_date(run) or "n/a"
    analysis_date_safe = _escape_html(analysis_date)
    range_safe = _escape_html(_resolve_run_range(run))
    created_at = _escape_html(str(run.get("created_at") or ""))
    completed_at = _escape_html(str(run.get("completed_at") or ""))

    action = str(
        (result.get("action") if isinstance(result, dict) else None)
        or (partial.get("action") if isinstance(partial, dict) else None)
        or "n/a"
    )
    action_safe = _escape_html(action)
    action_bg = _action_color(action)

    confidence_raw = (
        (result.get("confidence") if isinstance(result, dict) else None)
        or (partial.get("confidence") if isinstance(partial, dict) else None)
    )
    if isinstance(confidence_raw, (int, float)):
        confidence_safe = _escape_html(f"{confidence_raw}%")
    else:
        confidence_safe = _escape_html(str(confidence_raw or "n/a"))

    summary = str(
        (result.get("summary") if isinstance(result, dict) else None)
        or (partial.get("summary") if isinstance(partial, dict) else None)
        or "No summary available."
    )
    summary_safe = _escape_html(summary)

    # ── Provider / model info ──────────────────────────────────────────────
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    provider_safe = _escape_html(
        str(params.get("llm_provider") or payload.get("provider") or "n/a")
    )
    deep_model_safe = _escape_html(
        str(params.get("deep_think_llm") or payload.get("deep_model") or "n/a")
    )
    quick_model_safe = _escape_html(
        str(params.get("quick_think_llm") or payload.get("quick_model") or "n/a")
    )

    # ── Reports ────────────────────────────────────────────────────────────
    reports: dict[str, Any] = {}
    if isinstance(result.get("reports"), dict):
        reports.update(result["reports"])
    elif isinstance(partial.get("reports"), dict):
        reports.update(partial["reports"])
    reports_html = _build_reports_html(reports)

    # ── Embedded chart ─────────────────────────────────────────────────────
    chart_data_uri = _embed_chart_png_as_base64(run_id)
    if chart_data_uri:
        chart_section = (
            '<div class="card">\n'
            "  <h2>📉 Price Chart</h2>\n"
            f'  <img src="{chart_data_uri}" alt="{ticker_safe} price chart" class="chart-img" />\n'
            "</div>"
        )
    else:
        chart_link = _absolute_url(f"/runs/{run_id}/price-chart.html")
        chart_section = (
            '<div class="card">\n'
            "  <h2>📉 Price Chart</h2>\n"
            f'  <p class="muted">Chart image not embedded. <a href="{_escape_html(chart_link)}">View interactive chart →</a></p>\n'
            "</div>"
        )

    # ── Artifact links (absolute URLs) ─────────────────────────────────────
    artifact_links = {
        "Market Data (JSON)": _absolute_url(f"/runs/{run_id}/market-data.json"),
        "Price Chart (JSON)": _absolute_url(f"/runs/{run_id}/price-chart.json"),
        "Price Chart (PNG)": _absolute_url(f"/runs/{run_id}/price-chart.png"),
        "Price Chart (Interactive)": _absolute_url(f"/runs/{run_id}/price-chart.html"),
    }
    artifact_items = "\n".join(
        f'        <li><a href="{_escape_html(url)}">{_escape_html(label)}</a></li>'
        for label, url in artifact_links.items()
    )

    # ── Raw result (collapsed by default for debugging) ────────────────────
    raw_result_json = (
        json.dumps(result, ensure_ascii=False, indent=2)
        if isinstance(result, dict)
        else str(result)
    )

    # ── Timings ────────────────────────────────────────────────────────────
    timings = run.get("timings") or {}
    stage_durations = timings.get("stage_durations_ms") or {}
    timings_html = ""
    if stage_durations:
        rows_html = "\n".join(
            f"          <tr><td>{_escape_html(name)}</td>"
            f"<td>{int(ms):,} ms</td></tr>"
            for name, ms in stage_durations.items()
        )
        timings_html = (
            '<div class="card">\n'
            "  <h2>⏱️ Timings</h2>\n"
            '  <table class="timings-table">\n'
            "    <tbody>\n"
            f"      {rows_html}\n"
            "    </tbody>\n"
            "  </table>\n"
            "</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{ticker_safe} Analysis — {analysis_date_safe}</title>
  <meta name="description" content="AI trading analysis for {ticker_safe} as of {analysis_date_safe}">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 24px 16px;
      background: #0f172a;
      color: #e2e8f0;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto,
                   'Helvetica Neue', Arial, sans-serif;
      line-height: 1.6;
    }}
    .wrap {{
      max-width: 1100px;
      margin: 0 auto;
    }}
    .card {{
      background: #111827;
      border-radius: 12px;
      padding: 20px 24px;
      margin-bottom: 16px;
      box-shadow: 0 4px 20px rgba(0,0,0,0.25);
    }}
    h1 {{ margin: 0 0 4px; font-size: 1.6em; }}
    h2 {{ margin: 0 0 14px; font-size: 1.2em; color: #94a3b8; }}
    a {{ color: #93c5fd; text-decoration: none; }}
    a:hover {{ text-decoration: underline; }}
    .muted {{ color: #64748b; }}

    /* ── Header strip ── */
    .header-strip {{
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 12px;
      margin-bottom: 12px;
    }}
    .header-strip h1 {{ flex: 1 0 auto; }}
    .badge {{
      display: inline-block;
      padding: 4px 14px;
      border-radius: 999px;
      font-size: 0.85em;
      font-weight: 600;
      color: #fff;
    }}

    /* ── Meta grid ── */
    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
      gap: 10px;
    }}
    .meta-item {{
      background: #1e293b;
      border-radius: 8px;
      padding: 10px 14px;
    }}
    .meta-label {{ font-size: 0.75em; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; }}
    .meta-value {{ font-size: 1em; font-weight: 600; color: #f1f5f9; margin-top: 2px; }}

    /* ── Decision card ── */
    .decision-card {{
      display: flex;
      flex-wrap: wrap;
      gap: 20px;
      align-items: flex-start;
    }}
    .decision-primary {{
      flex: 0 0 auto;
      text-align: center;
      min-width: 120px;
    }}
    .decision-action {{
      font-size: 1.6em;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }}
    .decision-confidence {{
      font-size: 0.9em;
      color: #94a3b8;
      margin-top: 4px;
    }}
    .decision-summary {{
      flex: 1 1 300px;
      white-space: pre-wrap;
      line-height: 1.7;
      color: #cbd5e1;
    }}

    /* ── Reports ── */
    .report-section {{
      border: 1px solid #1e293b;
      border-radius: 8px;
      margin-bottom: 10px;
      overflow: hidden;
    }}
    .report-section summary {{
      cursor: pointer;
      padding: 12px 16px;
      font-weight: 600;
      font-size: 0.95em;
      background: #1e293b;
      user-select: none;
    }}
    .report-section summary:hover {{ background: #263044; }}
    .report-body {{
      padding: 14px 18px;
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 0.9em;
      line-height: 1.75;
      color: #cbd5e1;
    }}

    /* ── Chart ── */
    .chart-img {{
      width: 100%;
      height: auto;
      border-radius: 8px;
      background: #fff;
    }}

    /* ── Timings ── */
    .timings-table {{
      width: 100%;
      border-collapse: collapse;
    }}
    .timings-table td {{
      padding: 6px 12px;
      border-bottom: 1px solid #1e293b;
      font-size: 0.88em;
    }}
    .timings-table td:first-child {{
      color: #94a3b8;
      width: 55%;
    }}
    .timings-table td:last-child {{
      text-align: right;
      font-variant-numeric: tabular-nums;
    }}

    /* ── Artifacts ── */
    .artifact-list {{ line-height: 2; }}
    .artifact-list li {{ list-style: none; }}
    .artifact-list li::before {{ content: "📎 "; }}

    /* ── Raw JSON ── */
    .raw-toggle summary {{
      cursor: pointer;
      font-size: 0.85em;
      color: #64748b;
      padding: 8px 0;
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #0b1220;
      padding: 14px;
      border-radius: 8px;
      overflow: auto;
      color: #94a3b8;
      font-size: 0.82em;
      max-height: 600px;
    }}

    /* ── Footer ── */
    footer {{
      margin-top: 32px;
      padding-top: 16px;
      border-top: 1px solid #1e293b;
      text-align: center;
      color: #475569;
      font-size: 0.78em;
    }}

    /* ── Print ── */
    @media print {{
      body {{ background: #fff; color: #1a1a1a; padding: 0; }}
      .card {{ box-shadow: none; border: 1px solid #e2e8f0; }}
      .report-section {{ break-inside: avoid; }}
      details[open] > summary {{ font-weight: bold; }}
      .meta-item {{ background: #f8fafc; }}
      .report-section summary {{ background: #f1f5f9; }}
      .report-body {{ color: #334155; }}
      a {{ color: #2563eb; }}
      pre {{ background: #f8fafc; color: #334155; }}
      .badge {{ border: 2px solid currentColor; }}
    }}

    @media (max-width: 640px) {{
      body {{ padding: 12px 8px; }}
      .card {{ padding: 14px 12px; }}
      .meta-grid {{ grid-template-columns: 1fr 1fr; }}
    }}
  </style>
</head>
<body>
  <div class="wrap">

    <!-- ════ Header ════ -->
    <div class="card">
      <div class="header-strip">
        <h1>{ticker_safe} — Analysis Report</h1>
        <span class="badge" style="background:{action_bg}">{action_safe}</span>
        <span class="badge" style="background:#334155">{status}</span>
      </div>
      <div class="meta-grid">
        <div class="meta-item">
          <div class="meta-label">Ticker</div>
          <div class="meta-value">{ticker_safe}</div>
        </div>
        <div class="meta-item">
          <div class="meta-label">Analysis Date</div>
          <div class="meta-value">{analysis_date_safe}</div>
        </div>
        <div class="meta-item">
          <div class="meta-label">Confidence</div>
          <div class="meta-value">{confidence_safe}</div>
        </div>
        <div class="meta-item">
          <div class="meta-label">Range</div>
          <div class="meta-value">{range_safe}</div>
        </div>
        <div class="meta-item">
          <div class="meta-label">Provider</div>
          <div class="meta-value">{provider_safe}</div>
        </div>
        <div class="meta-item">
          <div class="meta-label">Deep Model</div>
          <div class="meta-value">{deep_model_safe}</div>
        </div>
        <div class="meta-item">
          <div class="meta-label">Quick Model</div>
          <div class="meta-value">{quick_model_safe}</div>
        </div>
        <div class="meta-item">
          <div class="meta-label">Run ID</div>
          <div class="meta-value" style="font-size:0.72em;word-break:break-all">{run_id_safe}</div>
        </div>
      </div>
    </div>

    <!-- ════ Decision ════ -->
    <div class="card">
      <h2>🎯 Portfolio Decision</h2>
      <div class="decision-card">
        <div class="decision-primary">
          <div class="decision-action" style="color:{action_bg}">{action_safe}</div>
          <div class="decision-confidence">Confidence: {confidence_safe}</div>
        </div>
        <div class="decision-summary">{summary_safe}</div>
      </div>
    </div>

    <!-- ════ Chart ════ -->
    {chart_section}

    <!-- ════ Agent Reports ════ -->
    <div class="card">
      <h2>🤖 Agent Reports</h2>
      {reports_html}
    </div>

    <!-- ════ Timings ════ -->
    {timings_html}

    <!-- ════ Artifacts ════ -->
    <div class="card">
      <h2>📦 Artifacts</h2>
      <ul class="artifact-list">
{artifact_items}
      </ul>
    </div>

    <!-- ════ Raw Result (collapsed) ════ -->
    <div class="card">
      <details class="raw-toggle">
        <summary>Show raw engine result (JSON)</summary>
        <pre>{_escape_html(raw_result_json)}</pre>
      </details>
    </div>

    <footer>
      Generated by Trading Analysis Engine &middot;
      Built on <a href="https://github.com/TauricResearch/TradingAgents">TradingAgents</a>
      (arXiv:2412.20138) &middot; Not investment advice &middot;
      Created {created_at} &middot; Completed {completed_at}
    </footer>

  </div>
</body>
</html>
"""


def write_market_data_json_artifact(run_id: str) -> Path:
    payload = build_market_data_json_payload(run_id)
    path = _artifact_path(run_id, "market-data.json")
    _safe_write_text(path, _json_text(payload, indent=2))
    return path


def write_price_chart_json_artifact(run_id: str) -> Path:
    payload = build_price_chart_json_payload(run_id)
    new_path = _artifact_path(run_id, "price-chart.json")
    _safe_write_text(new_path, _json_text(payload, indent=2))
    return new_path


def write_price_chart_png_artifact(run_id: str) -> Path:
    content = _render_price_chart_png(run_id)

    new_path = _artifact_path(run_id, "price-chart.png")
    old_path = _artifact_path(run_id, "chart.png")

    _safe_write_bytes(new_path, content)
    _safe_write_bytes(old_path, content)

    return new_path


def write_price_chart_html_artifact(run_id: str) -> Path:
    html = _render_price_chart_html(run_id)

    new_path = _artifact_path(run_id, "price-chart.html")
    old_path = _artifact_path(run_id, "chart.html")

    _safe_write_text(new_path, html)
    _safe_write_text(old_path, html)

    return new_path


def write_result_html_artifact(run_id: str) -> Path:
    html = build_result_html_content(run_id)
    path = _artifact_path(run_id, "result.html")
    _safe_write_text(path, html)
    return path


def write_all_core_artifacts(run_id: str) -> dict[str, str]:
    market_data_path = write_market_data_json_artifact(run_id)
    price_chart_json_path = write_price_chart_json_artifact(run_id)
    price_chart_png_path = write_price_chart_png_artifact(run_id)
    price_chart_html_path = write_price_chart_html_artifact(run_id)
    result_html_path = write_result_html_artifact(run_id)

    return {
        "market_data": str(market_data_path),
        "price_chart_json": str(price_chart_json_path),
        "price_chart_png": str(price_chart_png_path),
        "price_chart_html": str(price_chart_html_path),
        "chart_png_legacy": str(_artifact_path(run_id, "chart.png")),
        "chart_html_legacy": str(_artifact_path(run_id, "chart.html")),
        "result_html": str(result_html_path),
    }


def get_market_data_json_content(run_id: str) -> Optional[bytes]:
    path = _artifact_path(run_id, "market-data.json")
    content = _safe_read_bytes(path)
    if content is not None:
        return content

    try:
        payload = build_market_data_json_payload(run_id)
    except Exception:
        return None

    content = _json_bytes(payload, indent=2)
    _safe_write_bytes(path, content)
    return content


def get_price_chart_json_content(run_id: str) -> Optional[bytes]:
    path = _artifact_path(run_id, "price-chart.json")
    content = _safe_read_bytes(path)
    if content is not None:
        return content

    try:
        payload = build_price_chart_json_payload(run_id)
    except Exception:
        return None

    content = _json_bytes(payload, indent=2)
    _safe_write_bytes(path, content)
    return content


def get_price_chart_png_bytes(run_id: str) -> Optional[bytes]:
    new_path = _artifact_path(run_id, "price-chart.png")
    content = _safe_read_bytes(new_path)
    if content is not None:
        return content

    legacy_path = _artifact_path(run_id, "chart.png")
    legacy_content = _safe_read_bytes(legacy_path)
    if legacy_content is not None:
        _safe_write_bytes(new_path, legacy_content)
        return legacy_content

    try:
        content = _render_price_chart_png(run_id)
    except Exception:
        return None

    _safe_write_bytes(new_path, content)
    _safe_write_bytes(legacy_path, content)
    return content


def get_chart_png_bytes(run_id: str) -> Optional[bytes]:
    legacy_path = _artifact_path(run_id, "chart.png")
    content = _safe_read_bytes(legacy_path)
    if content is not None:
        return content

    return get_price_chart_png_bytes(run_id)


def get_price_chart_html_content(run_id: str) -> Optional[str]:
    new_path = _artifact_path(run_id, "price-chart.html")
    content = _safe_read_text(new_path)
    if content is not None:
        return content

    legacy_path = _artifact_path(run_id, "chart.html")
    legacy_content = _safe_read_text(legacy_path)
    if legacy_content is not None:
        _safe_write_text(new_path, legacy_content)
        return legacy_content

    try:
        html = _render_price_chart_html(run_id)
    except Exception:
        return None

    _safe_write_text(new_path, html)
    _safe_write_text(legacy_path, html)
    return html


def get_chart_html_content(run_id: str) -> Optional[str]:
    legacy_path = _artifact_path(run_id, "chart.html")
    content = _safe_read_text(legacy_path)
    if content is not None:
        return content

    return get_price_chart_html_content(run_id)


def get_result_html_content(run_id: str) -> Optional[str]:
    path = _artifact_path(run_id, "result.html")
    content = _safe_read_text(path)
    if content is not None:
        return content

    try:
        content = build_result_html_content(run_id)
    except Exception:
        return None

    _safe_write_text(path, content)
    return content

# ---------------------------------------------------------------------------
# Legacy compatibility wrappers
# ---------------------------------------------------------------------------

def save_market_data_json_content(run_id: str) -> Optional[bytes]:
    return get_market_data_json_content(run_id)


def save_price_chart_json_content(run_id: str) -> Optional[bytes]:
    return get_price_chart_json_content(run_id)


def save_price_chart_png_content(run_id: str) -> Optional[bytes]:
    return get_price_chart_png_bytes(run_id)


def save_chart_png_content(run_id: str) -> Optional[bytes]:
    return get_chart_png_bytes(run_id)


def save_price_chart_html_content(run_id: str) -> Optional[str]:
    return get_price_chart_html_content(run_id)


def save_chart_html_content(run_id: str) -> Optional[str]:
    return get_chart_html_content(run_id)


def save_result_html_content(run_id: str) -> Optional[str]:
    return get_result_html_content(run_id)


def save_market_data_json_artifact(run_id: str) -> Path:
    return write_market_data_json_artifact(run_id)


def save_price_chart_json_artifact(run_id: str) -> Path:
    return write_price_chart_json_artifact(run_id)


def save_price_chart_png_artifact_legacy(run_id: str) -> Path:
    return write_price_chart_png_artifact(run_id)


def save_price_chart_html_artifact(run_id: str) -> Path:
    return write_price_chart_html_artifact(run_id)


def save_chart_png_artifact(run_id: str) -> Path:
    return write_price_chart_png_artifact(run_id)


def save_chart_html_artifact(run_id: str) -> Path:
    return write_price_chart_html_artifact(run_id)