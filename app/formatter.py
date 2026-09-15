import re
from datetime import datetime, timezone
from typing import Any


DEFAULT_REPORTS = {
    "market_report": "",
    "sentiment_report": "",
    "bull_report": "",
    "bear_report": "",
    "investment_plan": "",
    "trader_plan": "",
    "risk_aggressive": "",
    "risk_conservative": "",
    "risk_neutral": "",
    "final_decision": "",
}


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return str(value).strip()


def _get_nested(d: dict[str, Any], *keys: str, default=None):
    cur = d
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur


def _search_first_text(raw: dict[str, Any], candidate_keys: list[str]) -> str:
    """
    Search shallow and nested structures for the first usable text value.
    """
    for key in candidate_keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    sections = raw.get("sections", {})
    if isinstance(sections, dict):
        for key in candidate_keys:
            value = sections.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    reports = raw.get("reports", {})
    if isinstance(reports, dict):
        for key in candidate_keys:
            value = reports.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    return ""


def _extract_action(text: str) -> str:
    text_upper = text.upper()

    if "STRONG BUY" in text_upper:
        return "Buy"
    if "BUY" in text_upper:
        return "Buy"
    if "STRONG SELL" in text_upper:
        return "Sell"
    if "SELL" in text_upper:
        return "Sell"
    if "HOLD" in text_upper:
        return "Hold"

    return "Hold"


def _extract_confidence(text: str) -> int:
    patterns = [
        r"confidence[:\s]+(\d{1,3})\s*%",
        r"(\d{1,3})\s*%\s*confidence",
        r"confidence[:\s]+(\d{1,3})",
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            val = int(m.group(1))
            return max(0, min(100, val))
    return 0


def _extract_price_after_label(text: str, labels: list[str]) -> float | None:
    for label in labels:
        pattern = rf"{label}[:\s]+\$?([0-9]+(?:\.[0-9]+)?)"
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            try:
                return float(m.group(1))
            except Exception:
                pass
    return None


def _extract_bullets(section_text: str) -> list[str]:
    lines = section_text.splitlines()
    out: list[str] = []

    for line in lines:
        s = line.strip()
        if not s:
            continue
        if re.match(r"^[-*•]\s+", s):
            out.append(re.sub(r"^[-*•]\s+", "", s).strip())
        elif re.match(r"^\d+[.)]\s+", s):
            out.append(re.sub(r"^\d+[.)]\s+", "", s).strip())

    return out[:5]


def _extract_section_block(text: str, section_names: list[str]) -> str:
    if not text.strip():
        return ""

    lines = text.splitlines()
    captured: list[str] = []
    active = False

    normalized_targets = [s.lower().strip() for s in section_names]

    for line in lines:
        stripped = line.strip()
        lowered = stripped.lower().rstrip(":")
        if lowered in normalized_targets:
            active = True
            continue

        if active:
            if stripped and stripped.endswith(":") and stripped.lower().rstrip(":") not in normalized_targets:
                break
            captured.append(line)

    return "\n".join(captured).strip()


def extract_reports(raw_result: dict[str, Any]) -> dict[str, str]:
    """
    Produce the report dictionary expected by the frontend.
    """
    reports = dict(DEFAULT_REPORTS)

    reports["market_report"] = _search_first_text(
        raw_result,
        ["market_report", "market", "marketAnalysis", "market_analysis"],
    )
    reports["sentiment_report"] = _search_first_text(
        raw_result,
        ["sentiment_report", "sentiment", "sentimentAnalysis", "sentiment_analysis"],
    )
    reports["bull_report"] = _search_first_text(
        raw_result,
        ["bull_report", "bull", "bull_case", "bullCase"],
    )
    reports["bear_report"] = _search_first_text(
        raw_result,
        ["bear_report", "bear", "bear_case", "bearCase"],
    )
    reports["investment_plan"] = _search_first_text(
        raw_result,
        ["investment_plan", "investment", "investor_plan", "long_term_plan"],
    )
    reports["trader_plan"] = _search_first_text(
        raw_result,
        ["trader_plan", "trading_plan", "trader", "short_term_plan"],
    )
    reports["risk_aggressive"] = _search_first_text(
        raw_result,
        ["risk_aggressive", "aggressive", "aggressive_plan"],
    )
    reports["risk_conservative"] = _search_first_text(
        raw_result,
        ["risk_conservative", "conservative", "conservative_plan"],
    )
    reports["risk_neutral"] = _search_first_text(
        raw_result,
        ["risk_neutral", "neutral", "neutral_plan"],
    )
    reports["final_decision"] = _search_first_text(
        raw_result,
        ["final_decision", "decision", "finalDecision", "summary"],
    )

    # fallback: if engine returned one giant combined report
    combined = _safe_str(raw_result.get("full_report") or raw_result.get("report") or raw_result.get("output"))
    if combined:
        for key in reports:
            if not reports[key]:
                reports[key] = combined

    return reports


def extract_decision(raw_result: dict[str, Any], prices: list[dict[str, Any]]) -> dict[str, Any]:
    reports = extract_reports(raw_result)
    final_text = reports.get("final_decision", "") or ""

    latest_close = None
    if prices:
        latest_close = prices[-1].get("close")

    explicit_action = _safe_str(
        _get_nested(raw_result, "decision_structured", "action")
        or _get_nested(raw_result, "decision", "action")
        or raw_result.get("action")
    )

    action = explicit_action if explicit_action else _extract_action(final_text)
    action = action.title() if action else "Hold"
    if action not in {"Buy", "Hold", "Sell"}:
        action = "Hold"

    confidence = (
        _get_nested(raw_result, "decision_structured", "confidence")
        or _get_nested(raw_result, "decision", "confidence")
        or raw_result.get("confidence")
    )
    try:
        confidence = int(confidence)
    except Exception:
        confidence = _extract_confidence(final_text)

    confidence = max(0, min(100, int(confidence)))

    consensus = _safe_str(
        _get_nested(raw_result, "decision_structured", "consensus")
        or _get_nested(raw_result, "decision", "consensus")
        or raw_result.get("consensus")
    ) or ("High" if confidence >= 75 else "Medium" if confidence >= 45 else "Low")

    entry_price = (
        _get_nested(raw_result, "decision_structured", "entryPrice")
        or _get_nested(raw_result, "decision", "entryPrice")
        or raw_result.get("entryPrice")
    )
    stop_loss = (
        _get_nested(raw_result, "decision_structured", "stopLoss")
        or _get_nested(raw_result, "decision", "stopLoss")
        or raw_result.get("stopLoss")
    )

    try:
        entry_price = float(entry_price) if entry_price is not None else None
    except Exception:
        entry_price = _extract_price_after_label(final_text, ["entry price", "entry", "buy below"])

    try:
        stop_loss = float(stop_loss) if stop_loss is not None else None
    except Exception:
        stop_loss = _extract_price_after_label(final_text, ["stop loss", "stop", "cut loss"])

    if entry_price is None and action == "Buy":
        entry_price = latest_close

    position_sizing = _safe_str(
        _get_nested(raw_result, "decision_structured", "positionSizing")
        or _get_nested(raw_result, "decision", "positionSizing")
        or raw_result.get("positionSizing")
    )

    executive_summary = _safe_str(
        _get_nested(raw_result, "decision_structured", "executiveSummary")
        or _get_nested(raw_result, "decision", "executiveSummary")
    )
    if not executive_summary:
        executive_summary = final_text[:500].strip()

    bull_points = _extract_bullets(reports.get("bull_report", ""))
    bear_points = _extract_bullets(reports.get("bear_report", ""))

    if not bull_points and reports.get("bull_report"):
        bull_points = [reports["bull_report"][:200].strip()]
    if not bear_points and reports.get("bear_report"):
        bear_points = [reports["bear_report"][:200].strip()]

    return {
        "action": action,
        "rating": action,
        "confidence": confidence,
        "consensus": consensus,
        "entryPrice": entry_price,
        "stopLoss": stop_loss,
        "positionSizing": position_sizing,
        "executiveSummary": executive_summary,
        "bullPoints": bull_points[:5],
        "bearPoints": bear_points[:5],
    }


def infer_stances(
    raw_result: dict[str, Any],
    analysts: list[str],
    decision: dict[str, Any],
    reports: dict[str, str],
) -> list[dict[str, Any]]:
    stance_map = raw_result.get("stances", {})
    out: list[dict[str, Any]] = []

    default_stance = {
        "Buy": "bullish",
        "Sell": "bearish",
        "Hold": "neutral",
    }.get(decision.get("action", "Hold"), "neutral")

    for analyst in analysts:
        raw = None
        if isinstance(stance_map, dict):
            raw = stance_map.get(analyst)

        stance = _safe_str(raw).lower()
        if stance not in {"bullish", "bearish", "neutral"}:
            text_blob = " ".join(
                [
                    reports.get("market_report", ""),
                    reports.get("sentiment_report", ""),
                    reports.get("bull_report", ""),
                    reports.get("bear_report", ""),
                    reports.get("final_decision", ""),
                ]
            ).lower()

            if "bear" in analyst.lower():
                stance = "bearish"
            elif "bull" in analyst.lower():
                stance = "bullish"
            elif "sentiment" in analyst.lower() and "negative" in text_blob:
                stance = "bearish"
            elif "sentiment" in analyst.lower() and "positive" in text_blob:
                stance = "bullish"
            else:
                stance = default_stance

        out.append(
            {
                "agent": analyst,
                "stance": stance,
            }
        )

    return out


def build_agent_progress(analysts: list[str], raw_result: dict[str, Any]) -> list[dict[str, Any]]:
    finished_agents = raw_result.get("finished_agents", [])
    notes = raw_result.get("agent_notes", {})
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")

    out = []
    for analyst in analysts:
        done = analyst in finished_agents if isinstance(finished_agents, list) else False
        note = notes.get(analyst) if isinstance(notes, dict) else None

        out.append(
            {
                "team": "Analyst Team",
                "agent": analyst,
                "status": "completed" if done else "completed",
                "time": now,
                "note": note,
            }
        )
    return out


def build_stats(raw_result: dict[str, Any], started_at: datetime) -> dict[str, Any]:
    stats = raw_result.get("stats", {})
    if not isinstance(stats, dict):
        stats = {}

    runtime_seconds = max(0, int((datetime.now(timezone.utc) - started_at).total_seconds()))

    reports = extract_reports(raw_result)
    reports_done = sum(1 for v in reports.values() if _safe_str(v))

    llm_calls = stats.get("llmCalls", stats.get("llm_calls", 0))
    tool_calls = stats.get("toolCalls", stats.get("tool_calls", 0))
    tokens_in = stats.get("tokensIn", stats.get("tokens_in", 0))
    tokens_out = stats.get("tokensOut", stats.get("tokens_out", 0))

    try:
        llm_calls = int(llm_calls)
    except Exception:
        llm_calls = 0

    try:
        tool_calls = int(tool_calls)
    except Exception:
        tool_calls = 0

    try:
        tokens_in = int(tokens_in)
    except Exception:
        tokens_in = 0

    try:
        tokens_out = int(tokens_out)
    except Exception:
        tokens_out = 0

    return {
        "llmCalls": llm_calls,
        "toolCalls": tool_calls,
        "tokensIn": tokens_in,
        "tokensOut": tokens_out,
        "reportsDone": reports_done,
        "reportsTotal": 4,
        "runtimeSeconds": runtime_seconds,
    }