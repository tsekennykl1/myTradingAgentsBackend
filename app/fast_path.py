"""Speed optimizations for TradingAgents runs.

Two independent optimizations, both opt-out via environment variables:

1. ``run_analysts_in_parallel`` — the stock graph chains the selected analysts
   one after another, so wall time is the sum of four LLM+tool loops. Here each
   analyst runs in its own compiled single-analyst graph on its own thread with
   its own message history, then the merged reports are fed into a "tail" graph
   that starts at the Bull Researcher. Nothing about the prompts, tools or the
   downstream pipeline changes — only the scheduling.

2. ``install_vendor_cache`` — every data tool funnels through
   ``tradingagents.dataflows.interface.route_to_vendor``. Wrapping it with a
   process-wide TTL cache means repeated runs for the same ticker+date (and the
   repeated calls a single run makes) skip the network entirely.

Both helpers are defensive: any structural drift in the installed
TradingAgents package raises, and the caller falls back to the stock path.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Optional

_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_INSTALLED = False


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _cache_key(name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
    try:
        payload = json.dumps([name, args, sorted(kwargs.items())], default=str, sort_keys=True)
    except Exception:
        payload = repr((name, args, kwargs))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def install_vendor_cache(import_module: Callable[[str], Any]) -> bool:
    """Memoize vendor data lookups per ticker+date for TTL seconds."""

    global _CACHE_INSTALLED

    if _CACHE_INSTALLED:
        return True
    if not _env_flag("TRADINGAGENTS_DATA_CACHE", True):
        return False

    ttl = float(os.getenv("TRADINGAGENTS_DATA_CACHE_TTL", "21600"))

    interface = import_module("tradingagents.dataflows.interface")
    original = getattr(interface, "route_to_vendor", None)
    if not callable(original):
        return False

    def cached_route_to_vendor(func_name, *args, **kwargs):
        key = _cache_key(str(func_name), args, kwargs)
        now = time.time()
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
        value = original(func_name, *args, **kwargs)
        with _CACHE_LOCK:
            _CACHE[key] = (now, value)
            if len(_CACHE) > 512:
                oldest = sorted(_CACHE.items(), key=lambda item: item[1][0])[:128]
                for stale_key, _ in oldest:
                    _CACHE.pop(stale_key, None)
        return value

    cached_route_to_vendor.__wrapped__ = original  # type: ignore[attr-defined]
    interface.route_to_vendor = cached_route_to_vendor
    _CACHE_INSTALLED = True
    print(f"[fast_path] vendor data cache installed (ttl={ttl:.0f}s)", flush=True)
    return True


ANALYST_REPORT_KEYS = {
    "market": "market_report",
    "social": "sentiment_report",
    "news": "news_report",
    "fundamentals": "fundamentals_report",
}


def parallel_enabled() -> bool:
    return _env_flag("TRADINGAGENTS_PARALLEL_ANALYSTS", True)


def _build_analyst_branch(graph: Any, spec: Any, agent_node: Any, import_module: Callable[[str], Any]):
    langgraph = import_module("langgraph.graph")
    agent_utils = import_module("tradingagents.agents.utils.agent_utils")
    states = import_module("tradingagents.agents.utils.agent_states")

    workflow = langgraph.StateGraph(states.AgentState)
    workflow.add_node(spec.agent_node, agent_node)
    workflow.add_node(spec.clear_node, agent_utils.create_msg_delete())
    workflow.add_node(spec.tool_node, graph.tool_nodes[spec.key])
    workflow.add_edge(langgraph.START, spec.agent_node)
    workflow.add_conditional_edges(
        spec.agent_node,
        getattr(graph.conditional_logic, f"should_continue_{spec.key}"),
        [spec.tool_node, spec.clear_node],
    )
    workflow.add_edge(spec.tool_node, spec.agent_node)
    workflow.add_edge(spec.clear_node, langgraph.END)
    return workflow.compile()


def _build_tail_graph(graph: Any, import_module: Callable[[str], Any]):
    """Everything from the Bull Researcher onward, unchanged in shape."""

    langgraph = import_module("langgraph.graph")
    states = import_module("tradingagents.agents.utils.agent_states")
    agents = import_module("tradingagents.agents")
    setup = import_module("tradingagents.graph.setup")

    quick = graph.quick_thinking_llm
    deep = graph.deep_thinking_llm

    workflow = langgraph.StateGraph(states.AgentState)
    workflow.add_node("Bull Researcher", agents.create_bull_researcher(quick))
    workflow.add_node("Bear Researcher", agents.create_bear_researcher(quick))
    workflow.add_node("Research Manager", agents.create_research_manager(deep))
    workflow.add_node("Trader", agents.create_trader(quick))
    workflow.add_node("Aggressive Analyst", agents.create_aggressive_debator(quick))
    workflow.add_node("Conservative Analyst", agents.create_conservative_debator(quick))
    workflow.add_node("Neutral Analyst", agents.create_neutral_debator(quick))
    workflow.add_node("Portfolio Manager", agents.create_portfolio_manager(deep))

    workflow.add_edge(langgraph.START, "Bull Researcher")
    for debate_node in ("Bull Researcher", "Bear Researcher"):
        workflow.add_conditional_edges(
            debate_node,
            graph.conditional_logic.should_continue_debate,
            setup.DEBATE_PATH_MAP,
        )
    workflow.add_edge("Research Manager", "Trader")
    workflow.add_edge("Trader", "Aggressive Analyst")
    for risk_node in ("Aggressive Analyst", "Conservative Analyst", "Neutral Analyst"):
        workflow.add_conditional_edges(
            risk_node,
            graph.conditional_logic.should_continue_risk_analysis,
            setup.RISK_ANALYSIS_PATH_MAP,
        )
    workflow.add_edge("Portfolio Manager", langgraph.END)
    return workflow.compile()


def run_analysts_in_parallel(
    graph: Any,
    ticker: str,
    analysis_date: str,
    asset_type: str,
    selected_analysts: tuple[str, ...],
    import_module: Callable[[str], Any],
    extract_reports: Callable[[Any], dict[str, str]],
    on_update: Optional[Callable[[str, Any], None]] = None,
) -> tuple[dict[str, Any], Any]:
    """Run the selected analysts concurrently, then the rest of the pipeline.

    Returns ``(final_state, decision)`` exactly like ``graph.propagate``.
    """

    analyst_execution = import_module("tradingagents.graph.analyst_execution")
    agents = import_module("tradingagents.agents")

    plan = analyst_execution.build_analyst_execution_plan(selected_analysts)

    factories = {
        "market": lambda: agents.create_market_analyst(graph.quick_thinking_llm),
        "social": lambda: agents.create_sentiment_analyst(graph.quick_thinking_llm),
        "news": lambda: agents.create_news_analyst(graph.quick_thinking_llm),
        "fundamentals": lambda: agents.create_fundamentals_analyst(graph.quick_thinking_llm),
    }

    instrument_context = ""
    resolver = getattr(graph, "resolve_instrument_context", None)
    if callable(resolver):
        try:
            instrument_context = resolver(ticker, asset_type) or ""
        except Exception as exc:  # identity resolution is best-effort
            print(f"[fast_path] instrument context unavailable: {exc}", flush=True)

    base_state = graph.propagator.create_initial_state(
        ticker,
        analysis_date,
        asset_type=asset_type,
        instrument_context=instrument_context,
    )
    graph_args = graph.propagator.get_graph_args()
    invoke_config = graph_args.get("config", {})

    branches = {
        spec.key: _build_analyst_branch(graph, spec, factories[spec.key](), import_module)
        for spec in plan.specs
    }

    def run_branch(spec):
        started = time.monotonic()
        state = branches[spec.key].invoke(dict(base_state), config=invoke_config)
        duration = time.monotonic() - started
        print(f"[fast_path] {spec.agent_node} finished in {duration:.1f}s", flush=True)
        return spec, state

    merged: dict[str, Any] = dict(base_state)
    errors: list[str] = []

    with ThreadPoolExecutor(max_workers=max(1, len(plan.specs))) as pool:
        for spec, state in pool.map(run_branch, plan.specs):
            report = state.get(spec.report_key) if isinstance(state, dict) else None
            if report:
                merged[spec.report_key] = report
                if on_update:
                    try:
                        on_update(spec.report_key, report)
                    except Exception as exc:
                        errors.append(f"{spec.report_key} publish failed: {exc}")

    for key in ("market_report", "sentiment_report", "news_report", "fundamentals_report"):
        merged.setdefault(key, "")
        if merged[key] is None:
            merged[key] = ""

    # Downstream agents read the report fields, not the analyst chatter.
    merged["messages"] = [("human", ticker)]

    tail = _build_tail_graph(graph, import_module)
    final_state: dict[str, Any] = dict(merged)
    for chunk in tail.stream(merged, **graph_args):
        if not isinstance(chunk, dict):
            continue
        final_state.update(chunk)
        if on_update:
            for report_name, content in extract_reports(chunk).items():
                try:
                    on_update(report_name, content)
                except Exception as exc:
                    errors.append(f"{report_name} publish failed: {exc}")

    if errors:
        print(f"[fast_path] publish warnings: {' | '.join(errors)}", flush=True)

    return final_state, final_state.get("final_trade_decision")
