from __future__ import annotations

import json
import os
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


def build_result_html_content(run_id: str) -> str:
    run = _resolve_run(run_id)
    if not run:
        raise ValueError(f"Run not found: {run_id}")

    payload = run.get("payload") or {}
    partial = run.get("partial_result") or {}
    result = run.get("result") or {}

    run_id_html = _escape_html(run_id)
    status_html = _escape_html(str(run.get("status", "unknown")))
    ticker_html = _escape_html(_resolve_run_symbol(run) or "n/a")
    analysis_date_html = _escape_html(_resolve_run_analysis_date(run) or "n/a")
    range_html = _escape_html(_resolve_run_range(run))

    action_html = _escape_html(
        str(
            (result.get("action") if isinstance(result, dict) else None)
            or (partial.get("action") if isinstance(partial, dict) else None)
            or "n/a"
        )
    )
    confidence_html = _escape_html(
        str(
            (result.get("confidence") if isinstance(result, dict) else None)
            or (partial.get("confidence") if isinstance(partial, dict) else None)
            or "n/a"
        )
    )
    summary_html = _escape_html(
        str(
            (result.get("summary") if isinstance(result, dict) else None)
            or (partial.get("summary") if isinstance(partial, dict) else None)
            or "No summary available."
        )
    )

    raw_result = json.dumps(result, ensure_ascii=False, indent=2) if isinstance(result, dict) else str(result)
    raw_payload = json.dumps(payload, ensure_ascii=False, indent=2) if isinstance(payload, dict) else str(payload)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Run Result - {run_id_html}</title>
  <style>
    body {{
      margin: 0;
      padding: 24px;
      background: #0f172a;
      color: #e2e8f0;
      font-family: Arial, sans-serif;
    }}
    .wrap {{
      max-width: 1100px;
      margin: 0 auto;
    }}
    .card {{
      background: #111827;
      border-radius: 12px;
      padding: 20px;
      margin-bottom: 16px;
      box-shadow: 0 10px 30px rgba(0,0,0,0.25);
    }}
    h1, h2 {{
      margin-top: 0;
    }}
    .meta {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
      gap: 12px;
    }}
    .pill {{
      display: inline-block;
      padding: 6px 10px;
      border-radius: 999px;
      background: #1f2937;
      font-size: 14px;
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      background: #0b1220;
      padding: 12px;
      border-radius: 8px;
      overflow: auto;
      color: #cbd5e1;
    }}
    a {{
      color: #93c5fd;
      text-decoration: none;
    }}
    a:hover {{
      text-decoration: underline;
    }}
    ul {{
      line-height: 1.8;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="card">
      <h1>Analysis Result</h1>
      <div class="meta">
        <div><span class="pill">Run ID: {run_id_html}</span></div>
        <div><span class="pill">Status: {status_html}</span></div>
        <div><span class="pill">Ticker: {ticker_html}</span></div>
        <div><span class="pill">Analysis Date: {analysis_date_html}</span></div>
        <div><span class="pill">Range: {range_html}</span></div>
        <div><span class="pill">Action: {action_html}</span></div>
        <div><span class="pill">Confidence: {confidence_html}</span></div>
      </div>
    </div>

    <div class="card">
      <h2>Summary</h2>
      <pre>{summary_html}</pre>
    </div>

    <div class="card">
      <h2>Artifacts</h2>
      <ul>
        <li><a href="/runs/{run_id_html}/market-data">market-data.json</a></li>
        <li><a href="/runs/{run_id_html}/market-data.json">market-data.json (alias)</a></li>
        <li><a href="/runs/{run_id_html}/price-chart.json">price-chart.json</a></li>
        <li><a href="/runs/{run_id_html}/price-chart.png">price-chart.png</a></li>
        <li><a href="/runs/{run_id_html}/price-chart.html">price-chart.html</a></li>
        <li><a href="/runs/{run_id_html}/chart.png">chart.png (legacy)</a></li>
        <li><a href="/runs/{run_id_html}/chart.html">chart.html (legacy)</a></li>
        <li><a href="/runs/{run_id_html}/result.html">result.html</a></li>
      </ul>
    </div>

    <div class="card">
      <h2>Raw Result</h2>
      <pre>{_escape_html(raw_result)}</pre>
    </div>

    <div class="card">
      <h2>Run Payload</h2>
      <pre>{_escape_html(raw_payload)}</pre>
    </div>
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