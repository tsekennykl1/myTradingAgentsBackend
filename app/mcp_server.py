"""Public MCP tools for the TradingAgents analysis service.

The MCP layer is deliberately thin: tools validate bounded inputs, then call the
same service functions used by the FastAPI routes. Long analyses stay in the
existing worker queue, so ``create_analysis`` returns a run id immediately.

Access is gated by a shared key: every HTTP request must send MCP_ACCESS_KEY as
``Authorization: Bearer <key>`` or ``X-MCP-Key: <key>``. When MCP_ACCESS_KEY is
unset the endpoint refuses every request (fail closed). Set MCP_ENABLED=0 to
disable the endpoint, or MCP_ALLOWED_TOOLS to a comma-separated allow-list when
a deployment should be read-only. Never add provider secrets or raw filesystem
access to this module.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import threading
import time
from collections import deque
from datetime import date
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

SERVER_NAME = "trading-agents"
ALL_TOOLS = {
    "create_analysis", "cancel_analysis", "list_analyses", "get_analysis",
    "get_analysis_status", "get_decision", "list_reports", "get_report",
    "get_chart_data", "get_market_snapshot", "get_earnings",
    "get_engine_info", "list_ai_models",
}
RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
REPORT_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,79}$")
TICKER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,23}$")
ANALYSTS = {"market", "social", "news", "fundamentals"}
STAGES = {
    "market", "social", "news", "fundamentals", "research_debate",
    "research_manager", "trader", "risk_debate", "portfolio_manager",
}
_CREATE_CALLS: deque[float] = deque()
_CREATE_LOCK = threading.Lock()


def _enabled() -> bool:
    return os.getenv("MCP_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}


def _allowed_tools() -> set[str]:
    raw = os.getenv("MCP_ALLOWED_TOOLS", "").strip()
    if not raw:
        return set(ALL_TOOLS)
    return {name.strip() for name in raw.split(",") if name.strip()} & ALL_TOOLS


def _require_tool(name: str) -> None:
    if not _enabled():
        raise ToolError("The MCP server is disabled by MCP_ENABLED.")
    if name not in _allowed_tools():
        raise ToolError(f"The '{name}' tool is disabled by MCP_ALLOWED_TOOLS.")


def _run_id(value: str) -> str:
    value = value.strip()
    if not RUN_ID_PATTERN.fullmatch(value):
        raise ToolError("Invalid run id.")
    return value


def _ticker(value: str) -> str:
    value = value.strip().upper()
    if not TICKER_PATTERN.fullmatch(value):
        raise ToolError("Ticker must contain 1-24 letters, numbers, dots, colons, or hyphens.")
    return value


def _report_name(value: str) -> str:
    value = value.strip()
    if not REPORT_PATTERN.fullmatch(value):
        raise ToolError("Invalid report name.")
    return value


def _public_base_url() -> str:
    return os.getenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000").strip().rstrip("/")


def _require_run(run_id: str) -> dict[str, Any]:
    from app.services.analyze_service import get_run

    run = get_run(_run_id(run_id))
    if not run:
        raise ToolError("Analysis not found.")
    return run


def _check_create_rate_limit() -> None:
    try:
        maximum = max(1, min(100, int(os.getenv("MCP_CREATE_RUNS_PER_HOUR", "12"))))
    except ValueError:
        maximum = 12
    now = time.monotonic()
    with _CREATE_LOCK:
        while _CREATE_CALLS and now - _CREATE_CALLS[0] >= 3600:
            _CREATE_CALLS.popleft()
        if len(_CREATE_CALLS) >= maximum:
            raise ToolError("Public analysis creation rate limit reached. Try again later.")
        _CREATE_CALLS.append(now)


def _json_artifact(content: bytes | None, missing: str) -> Any:
    if content is None:
        raise ToolError(missing)
    try:
        return json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ToolError("The stored artifact is not valid JSON.") from exc


mcp = FastMCP(
    SERVER_NAME,
    instructions=(
        "Tools for the TradingAgents financial-analysis engine. Analyses are asynchronous: "
        "call create_analysis, poll get_analysis_status using poll_after_ms, then read the "
        "decision, reports, and chart data. This public server has no user identity."
    ),
    streamable_http_path="/",
    stateless_http=True,
    json_response=True,
)


@mcp.tool(
    title="Create analysis",
    description="Queue a TradingAgents analysis and return its run id immediately.",
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True),
)
def create_analysis(
    ticker: str,
    analysis_date: str,
    provider: str,
    deep_model: str,
    quick_model: str,
    research_depth: Literal["Shallow", "Medium", "Deep"] = "Shallow",
    language: str = "English",
    analysts: list[str] | None = None,
    stages: list[str] | None = None,
) -> dict[str, Any]:
    """Queue one analysis. Provider credentials must already exist in the backend .env."""
    _require_tool("create_analysis")
    _check_create_rate_limit()
    symbol = _ticker(ticker)
    try:
        parsed_date = date.fromisoformat(analysis_date)
    except ValueError as exc:
        raise ToolError("analysis_date must use YYYY-MM-DD.") from exc
    if parsed_date > date.today():
        raise ToolError("analysis_date cannot be in the future.")
    provider = provider.strip().lower()
    deep_model = deep_model.strip()
    quick_model = quick_model.strip()
    language = language.strip()
    if not all((provider, deep_model, quick_model, language)):
        raise ToolError("provider, deep_model, quick_model, and language are required.")
    chosen_analysts = analysts if analysts is not None else ["market"]
    chosen_stages = stages if stages is not None else [
        "market", "research_debate", "research_manager", "trader",
        "risk_debate", "portfolio_manager",
    ]
    invalid_analysts = sorted(set(chosen_analysts) - ANALYSTS)
    invalid_stages = sorted(set(chosen_stages) - STAGES)
    if invalid_analysts:
        raise ToolError(f"Unknown analysts: {', '.join(invalid_analysts)}")
    if invalid_stages:
        raise ToolError(f"Unknown stages: {', '.join(invalid_stages)}")
    if not chosen_analysts:
        raise ToolError("Select at least one analyst.")

    payload = {
        "ticker": symbol,
        "analysis_date": analysis_date,
        "end_date": analysis_date,
        "provider": provider,
        "deep_model": deep_model,
        "quick_model": quick_model,
        "research_depth": research_depth,
        "language": language,
        "analysts": chosen_analysts,
        "selected_analysts": chosen_analysts,
        "stages": chosen_stages,
        "selected_stages": chosen_stages,
        "params": {
            "llm_provider": provider,
            "deep_think_llm": deep_model,
            "quick_think_llm": quick_model,
            "research_depth": research_depth,
            "language": language,
            "analysts": chosen_analysts,
            "selected_analysts": chosen_analysts,
            "stages": chosen_stages,
            "selected_stages": chosen_stages,
        },
    }
    from app.services.analyze_service import create_or_reuse_run, get_run

    try:
        created = create_or_reuse_run(payload)
    except ValueError as exc:
        raise ToolError(str(exc)) from exc
    run = get_run(created["run_id"])
    return {
        "run_id": created["run_id"],
        "reused": created["reused"],
        "status": (run or {}).get("status", "queued"),
        "poll_after_ms": (run or {}).get("poll_after_ms", 1500),
    }


@mcp.tool(title="Cancel analysis", description="Request cancellation of a queued or running analysis.", annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False))
def cancel_analysis(run_id: str) -> dict[str, Any]:
    _require_tool("cancel_analysis")
    run = _require_run(run_id)
    from app.services.analyze_service import cancel_run, get_run

    if not cancel_run(run["run_id"]):
        raise ToolError("The cancellation request failed.")
    updated = get_run(run["run_id"]) or run
    return {"run_id": run["run_id"], "status": updated.get("status"), "cancel_requested": True}


@mcp.tool(title="List analyses", description="List recent analyses without returning full report bodies.", annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def list_analyses(limit: int = 20, status: str | None = None) -> dict[str, Any]:
    _require_tool("list_analyses")
    if not 1 <= limit <= 100:
        raise ToolError("limit must be between 1 and 100.")
    from app.services.analyze_service import list_runs

    wanted = status.strip().lower() if status else None
    items = []
    for run in list_runs():
        if wanted and str(run.get("status", "")).lower() != wanted:
            continue
        result = run.get("result") if isinstance(run.get("result"), dict) else {}
        payload = run.get("payload") if isinstance(run.get("payload"), dict) else {}
        items.append({
            "run_id": run.get("run_id"), "status": run.get("status"),
            "ticker": result.get("ticker") or payload.get("ticker"),
            "analysis_date": result.get("analysis_date") or payload.get("analysis_date") or payload.get("end_date"),
            "created_at": run.get("created_at"), "started_at": run.get("started_at"),
            "completed_at": run.get("completed_at"), "decision_ready": run.get("decision_ready"),
            "reports_ready": run.get("reports_ready", []), "result_url": run.get("result_url"),
        })
        if len(items) >= limit:
            break
    return {"items": items, "count": len(items)}


@mcp.tool(title="Get analysis", description="Read one complete stored analysis record.", annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def get_analysis(run_id: str) -> dict[str, Any]:
    _require_tool("get_analysis")
    return _require_run(run_id)


@mcp.tool(title="Get analysis status", description="Read lightweight progress, readiness, timing, and polling information.", annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def get_analysis_status(run_id: str) -> dict[str, Any]:
    _require_tool("get_analysis_status")
    from app.services.analyze_service import get_run_status

    value = get_run_status(_run_id(run_id))
    if not value:
        raise ToolError("Analysis not found.")
    return value


@mcp.tool(title="Get decision", description="Read the portfolio decision for a completed or partially published analysis.", annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def get_decision(run_id: str) -> dict[str, Any]:
    _require_tool("get_decision")
    _require_run(run_id)
    from app.services.analyze_service import get_run_decision

    decision = get_run_decision(_run_id(run_id))
    if decision is None:
        raise ToolError("Decision is not available yet.")
    return decision


@mcp.tool(title="List reports", description="List report names currently published for an analysis.", annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def list_reports(run_id: str) -> dict[str, Any]:
    _require_tool("list_reports")
    _require_run(run_id)
    from app.services.analyze_service import get_run_reports

    reports = get_run_reports(_run_id(run_id)) or {}
    return {"run_id": run_id, "reports": sorted(reports), "count": len(reports)}


@mcp.tool(title="Get report", description="Read one published analyst or final report by name.", annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def get_report(run_id: str, report_name: str) -> dict[str, Any]:
    _require_tool("get_report")
    _require_run(run_id)
    from app.services.analyze_service import get_run_report

    name = _report_name(report_name)
    report = get_run_report(_run_id(run_id), name)
    if report is None:
        raise ToolError(f"Report '{name}' is not available.")
    return {"run_id": run_id, "report_name": name, "content": report}


@mcp.tool(title="Get chart data", description="Read structured prices, indicators, signals, and public chart links for an analysis.", annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def get_chart_data(run_id: str) -> dict[str, Any]:
    _require_tool("get_chart_data")
    run = _require_run(run_id)
    from app.artifacts import get_market_data_json_content, get_price_chart_json_content

    clean_id = run["run_id"]
    chart = _json_artifact(get_price_chart_json_content(clean_id), "Chart data is not available yet.")
    market = _json_artifact(get_market_data_json_content(clean_id), "Market data is not available yet.")
    base = _public_base_url()
    return {
        "run_id": clean_id,
        "chart": chart,
        "market_data": market,
        "links": {
            "interactive_chart": f"{base}/runs/{clean_id}/price-chart.html",
            "chart_image": f"{base}/runs/{clean_id}/price-chart.png",
            "detail_report": f"{base}/runs/{clean_id}/result.html",
        },
    }


@mcp.tool(title="Get market snapshot", description="Read the cached Alpha Vantage company profile and latest quote.", annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True))
def get_market_snapshot(ticker: str) -> dict[str, Any]:
    _require_tool("get_market_snapshot")
    from app.routes.market import company

    return company(ticker=_ticker(ticker))


@mcp.tool(title="Get earnings", description="Read cached quarterly and annual earnings for a ticker.", annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=True))
def get_earnings(ticker: str, quarters: int = 4) -> dict[str, Any]:
    _require_tool("get_earnings")
    if not 1 <= quarters <= 12:
        raise ToolError("quarters must be between 1 and 12.")
    from app.routes.market import earnings

    return earnings(ticker=_ticker(ticker), quarters=quarters)


@mcp.tool(title="Get engine info", description="Check engine and MCP availability without revealing configuration secrets.", annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def get_engine_info() -> dict[str, Any]:
    _require_tool("get_engine_info")
    from app.services.analyze_service import engine_available

    return {
        "service": "trading-analysis-api", "tradingagents_available": engine_available(),
        "mcp_transport": "streamable-http", "mcp_endpoint": f"{_public_base_url()}/mcp",
        "authentication": "none", "allowed_tools": sorted(_allowed_tools()),
    }


@mcp.tool(title="List AI models", description="List configured AI providers and supported models without returning API keys.", annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False))
def list_ai_models() -> dict[str, Any]:
    _require_tool("list_ai_models")
    from app.routes.providers import list_providers

    catalogue = list_providers()
    safe = []
    for item in catalogue.get("providers", []):
        safe.append({key: item.get(key) for key in (
            "provider", "label", "configured", "supports_balance", "deep_models",
            "quick_models", "default_deep_model", "default_quick_model",
        )})
    return {"active": catalogue.get("active"), "providers": safe}


def _access_key() -> str:
    """Shared key every MCP caller must present. Empty means 'refuse everything'."""
    return os.getenv("MCP_ACCESS_KEY", "").strip()


def _presented_key(headers: list[tuple[bytes, bytes]]) -> str:
    """Read the caller's key from Authorization: Bearer ... or X-MCP-Key."""
    for raw_name, raw_value in headers:
        name = raw_name.decode("latin-1").lower()
        value = raw_value.decode("latin-1").strip()
        if name == "x-mcp-key" and value:
            return value
        if name == "authorization" and value.lower().startswith("bearer "):
            return value[7:].strip()
    return ""


def _unauthorized_body(message: str) -> bytes:
    return json.dumps({"error": "unauthorized", "detail": message}).encode("utf-8")


class AccessKeyMiddleware:
    """ASGI guard in front of the MCP transport.

    Rejects any request without the shared key before the MCP session manager
    sees it, so an engine on a public address cannot be driven by strangers.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        expected = _access_key()
        if not expected:
            await self._deny(send, "This engine has no MCP_ACCESS_KEY configured, so MCP is closed.")
            return
        presented = _presented_key(scope.get("headers") or [])
        if not presented or not hmac.compare_digest(presented, expected):
            await self._deny(send, "Missing or invalid MCP access key.")
            return
        await self.app(scope, receive, send)

    async def _deny(self, send: Any, message: str) -> None:
        body = _unauthorized_body(message)
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"www-authenticate", b'Bearer realm="mcp"'),
            ],
        })
        await send({"type": "http.response.body", "body": body})


mcp_http_app = AccessKeyMiddleware(mcp.streamable_http_app())
