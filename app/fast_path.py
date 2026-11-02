"""Speed optimizations for TradingAgents runs (targets TradingAgents v0.7.x).

Two independent optimizations, both opt-out via environment variables:

1. ``run_analysts_in_parallel`` — runs each selected analyst in its own
   single-analyst graph (built with the engine's own ``_analyst_graph`` helper,
   so the tool-round cap and private message history are the engine's), next to
   the engine's Memory Log step, then feeds the merged reports into a "tail"
   graph that starts at the Bull Researcher. On top of the stock pipeline it
   adds per-stage progress events, optional stage skipping and the three risk
   viewpoints of each round run side by side.

2. ``install_vendor_cache`` — every data tool funnels through
   ``tradingagents.dataflows.router.route_to_vendor``. Wrapping it with a
   process-wide TTL cache means repeated runs for the same ticker+date (and the
   repeated calls a single run makes) skip the network entirely. Transient
   "vendor unavailable" answers are never cached.

Both helpers are defensive: any structural drift in the installed
TradingAgents package raises, and the caller falls back to the stock path.

TradingAgents reads its settings (vendors, output language) from a per-run
context variable. Worker threads do not inherit context variables, so every
thread started here runs inside a copy of the caller's context, opened under
the graph's own ``run_config``.
"""

from __future__ import annotations

import contextlib
import contextvars
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

#: Module that owns ``route_to_vendor`` (v0.5.1+), then the pre-0.5.1 name.
ROUTER_MODULES = ("tradingagents.dataflows.router", "tradingagents.dataflows.interface")
#: Modules that bind ``route_to_vendor`` by name at import time.
TOOL_MODULES = ("tradingagents.agents.tools",)
#: Answers that describe a vendor outage rather than the instrument.
_TRANSIENT_PREFIXES = ("DATA_UNAVAILABLE",)


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _vendor_settings(import_module: Callable[[str], Any]) -> Any:
    """The vendor chain in force, so a cached answer is never served for another chain."""
    try:
        config = import_module("tradingagents.dataflows.config").get_config()
        return [config.get("data_vendors"), config.get("tool_vendors")]
    except Exception:
        return None


def _cache_key(name: str, args: tuple[Any, ...], kwargs: dict[str, Any], vendors: Any = None) -> str:
    try:
        payload = json.dumps([name, args, sorted(kwargs.items()), vendors], default=str, sort_keys=True)
    except Exception:
        payload = repr((name, args, kwargs, vendors))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _is_transient(value: Any) -> bool:
    return isinstance(value, str) and value.lstrip().startswith(_TRANSIENT_PREFIXES)


def _first_module(import_module: Callable[[str], Any], names: tuple[str, ...]) -> Any:
    errors = []
    for name in names:
        try:
            return import_module(name)
        except Exception as exc:  # pragma: no cover - only the message is used
            errors.append(f"{name}: {exc}")
    raise ImportError("; ".join(errors))


def install_vendor_cache(import_module: Callable[[str], Any]) -> bool:
    """Memoize vendor data lookups per ticker+date for TTL seconds."""

    global _CACHE_INSTALLED

    if _CACHE_INSTALLED:
        return True
    if not _env_flag("TRADINGAGENTS_DATA_CACHE", True):
        return False

    ttl = float(os.getenv("TRADINGAGENTS_DATA_CACHE_TTL", "21600"))

    router = _first_module(import_module, ROUTER_MODULES)
    original = getattr(router, "route_to_vendor", None)
    if not callable(original):
        return False

    def cached_route_to_vendor(func_name, *args, **kwargs):
        key = _cache_key(str(func_name), args, kwargs, _vendor_settings(import_module))
        now = time.time()
        with _CACHE_LOCK:
            hit = _CACHE.get(key)
            if hit and now - hit[0] < ttl:
                return hit[1]
        value = original(func_name, *args, **kwargs)
        if _is_transient(value):
            return value
        with _CACHE_LOCK:
            _CACHE[key] = (now, value)
            if len(_CACHE) > 512:
                oldest = sorted(_CACHE.items(), key=lambda item: item[1][0])[:128]
                for stale_key, _ in oldest:
                    _CACHE.pop(stale_key, None)
        return value

    cached_route_to_vendor.__wrapped__ = original  # type: ignore[attr-defined]
    router.route_to_vendor = cached_route_to_vendor
    # The data tools import the function by name, so patching the router alone
    # would leave every tool call uncached.
    for name in TOOL_MODULES:
        try:
            module = import_module(name)
        except Exception:
            continue
        if getattr(module, "route_to_vendor", None) is original:
            module.route_to_vendor = cached_route_to_vendor
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


def _run_scope(graph: Any, import_module: Callable[[str], Any]):
    """The graph's own settings for every read made inside the block."""
    config = getattr(graph, "config", None)
    if not isinstance(config, dict):
        return contextlib.nullcontext()
    try:
        run_config = import_module("tradingagents.dataflows.config").run_config
    except Exception:
        return contextlib.nullcontext()
    return run_config(config)


def _in_context(pool: ThreadPoolExecutor, fn: Callable[..., Any], *args: Any):
    """Submit ``fn`` to run inside a copy of the *calling* thread's context."""
    return pool.submit(contextvars.copy_context().run, fn, *args)


def _max_tool_rounds(graph: Any) -> int:
    setup = getattr(graph, "graph_setup", None)
    value = getattr(setup, "max_tool_rounds", None)
    if value is None and isinstance(getattr(graph, "config", None), dict):
        value = graph.config.get("max_tool_rounds")
    return int(value or 20)


def _build_analyst_branch(graph: Any, spec: Any, agent_node: Any, import_module: Callable[[str], Any]):
    """One analyst as its own graph, exactly as the engine wires it."""
    setup = import_module("tradingagents.graph.setup")
    builder = getattr(setup, "_analyst_graph", None)
    if not callable(builder):
        raise RuntimeError(
            "installed TradingAgents has no graph.setup._analyst_graph; "
            "this fast path supports TradingAgents v0.7.x"
        )
    return builder(spec, agent_node, _max_tool_rounds(graph))


#: Stages of the tail pipeline the caller may opt out of.
TAIL_STAGES = ("research_debate", "research_manager", "trader", "risk_debate", "portfolio_manager")


def normalize_stages(stages: Any) -> Optional[set[str]]:
    """Return the enabled tail stages, or ``None`` when the caller opted out of nothing."""

    if stages is None:
        return None
    wanted = {str(item).strip().lower() for item in stages if str(item).strip()}
    return {stage for stage in TAIL_STAGES if stage in wanted}


def _passthrough(_state: Any) -> dict[str, Any]:
    """A skipped stage contributes nothing and makes no LLM call."""

    return {}


def _state_module(import_module: Callable[[str], Any]) -> Any:
    return _first_module(import_module, ("tradingagents.agents.state", "tradingagents.agents"))


def _build_tail_graph(
    graph: Any,
    import_module: Callable[[str], Any],
    enabled: Optional[set[str]] = None,
    on_stage: Optional[Callable[[str, str], None]] = None,
):
    """Everything from the Bull Researcher onward; skipped stages become no-ops.

    Nodes that the vendor's conditional path maps point at ("Research Manager",
    "Portfolio Manager") always exist, so a skipped stage is a passthrough node
    rather than a missing edge target. Skipped *debates* are routed around
    entirely, because their loop counters would otherwise never advance.
    """

    langgraph = import_module("langgraph.graph")
    states = _state_module(import_module)
    agents = import_module("tradingagents.agents")
    setup = import_module("tradingagents.graph.setup")

    quick = graph.quick_thinking_llm
    deep = graph.deep_thinking_llm

    def on(stage: str) -> bool:
        return enabled is None or stage in enabled

    def emit(stage: str, event: str) -> None:
        if on_stage:
            on_stage(stage, event)

    def timed(stage: str, node: Callable[[Any], dict[str, Any]], previous: Optional[str] = None):
        def wrapped(state: Any) -> dict[str, Any]:
            if previous:
                emit(previous, "completed")
            emit(stage, "started")
            try:
                result = node(state)
            except Exception:
                emit(stage, "error")
                raise
            emit(stage, "completed")
            return result
        return wrapped

    def begin(stage: str, node: Callable[[Any], dict[str, Any]]):
        def wrapped(state: Any) -> dict[str, Any]:
            emit(stage, "started")
            try:
                return node(state)
            except Exception:
                emit(stage, "error")
                raise
        return wrapped

    def risk_rounds(state: Any) -> dict[str, Any]:
        """Run each risk round from one snapshot, then merge in stable order."""
        emit("risk_debate", "started")
        current = dict(state)
        rounds = max(1, int(getattr(graph.conditional_logic, "max_risk_discuss_rounds", 1)))
        factories = (
            ("aggressive", agents.create_aggressive_debator),
            ("conservative", agents.create_conservative_debator),
            ("neutral", agents.create_neutral_debator),
        )
        nodes = [(name, factory(quick)) for name, factory in factories]
        try:
            for _round in range(rounds):
                snapshot = dict(current)
                snapshot["risk_debate_state"] = dict(current.get("risk_debate_state") or {})
                with ThreadPoolExecutor(max_workers=3) as pool:
                    futures = [
                        _in_context(pool, lambda item=item: (item[0], item[1](snapshot)))
                        for item in nodes
                    ]
                    outputs = [future.result() for future in futures]
                prior = dict(current.get("risk_debate_state") or {})
                arguments: list[str] = []
                for name, output in outputs:
                    risk = dict((output or {}).get("risk_debate_state") or {})
                    history_key = f"{name}_history"
                    response_key = f"current_{name}_response"
                    argument = str(risk.get(response_key) or "")
                    if argument:
                        arguments.append(argument)
                        prior[history_key] = str(prior.get(history_key) or "") + "\n" + argument
                        prior[response_key] = argument
                prior["history"] = str(prior.get("history") or "") + "\n" + "\n".join(arguments)
                prior["latest_speaker"] = "Neutral"
                prior["count"] = int(prior.get("count") or 0) + 3
                current["risk_debate_state"] = prior
        except Exception:
            emit("risk_debate", "error")
            raise
        emit("risk_debate", "completed")
        return {"risk_debate_state": current["risk_debate_state"]}

    workflow = langgraph.StateGraph(states.AgentState)
    if on("research_debate"):
        workflow.add_node("Bull Researcher", begin("research_debate", agents.create_bull_researcher(quick)))
        workflow.add_node("Bear Researcher", agents.create_bear_researcher(quick))
    workflow.add_node(
        "Research Manager",
        timed("research_manager", agents.create_research_manager(deep), "research_debate" if on("research_debate") else None)
        if on("research_manager")
        else (timed("research_debate", _passthrough) if on("research_debate") else _passthrough),
    )
    workflow.add_node("Trader", timed("trader", agents.create_trader(quick)) if on("trader") else _passthrough)
    if on("risk_debate"):
        workflow.add_node("Risk Analysts", risk_rounds)
    workflow.add_node(
        "Portfolio Manager",
        timed("portfolio_manager", agents.create_portfolio_manager(deep)) if on("portfolio_manager") else _passthrough,
    )

    if on("research_debate"):
        workflow.add_edge(langgraph.START, "Bull Researcher")
        for debate_node in ("Bull Researcher", "Bear Researcher"):
            workflow.add_conditional_edges(
                debate_node,
                graph.conditional_logic.should_continue_debate,
                setup.DEBATE_PATH_MAP,
            )
    else:
        workflow.add_edge(langgraph.START, "Research Manager")

    workflow.add_edge("Research Manager", "Trader")

    if on("risk_debate"):
        workflow.add_edge("Trader", "Risk Analysts")
        workflow.add_edge("Risk Analysts", "Portfolio Manager")
    else:
        workflow.add_edge("Trader", "Portfolio Manager")

    workflow.add_edge("Portfolio Manager", langgraph.END)
    return workflow.compile()


def create_run_state_compat(graph: Any, ticker: str, analysis_date: str, asset_type: str) -> dict[str, Any]:
    """The run's initial state, built the way ``propagate()`` builds it.

    v0.7.x validates the date and resolves the instrument identity as of the
    trade date in ``create_run_state``; older releases only had the propagator.
    """
    builder = getattr(graph, "create_run_state", None)
    if callable(builder):
        return builder(ticker, analysis_date, asset_type)

    instrument_context = ""
    resolver = getattr(graph, "resolve_instrument_context", None)
    if callable(resolver):
        try:
            instrument_context = resolver(ticker, asset_type) or ""
        except Exception as exc:  # identity resolution is best-effort
            print(f"[fast_path] instrument context unavailable: {exc}", flush=True)
    return create_initial_state_compat(
        graph.propagator,
        ticker,
        analysis_date,
        asset_type=asset_type,
        instrument_context=instrument_context,
    )


def decision_rating(final_state: Any, import_module: Callable[[str], Any]) -> Any:
    """The Portfolio Manager's rating (Buy / Overweight / Hold / Underweight / Sell / REVIEW)."""
    if not isinstance(final_state, dict):
        return None
    try:
        return import_module("tradingagents.agents.rating").run_rating(final_state)
    except Exception:
        return final_state.get("final_rating") or final_state.get("final_trade_decision")


def record_run(graph: Any, ticker: str, analysis_date: str, final_state: dict[str, Any]) -> None:
    """Log the run and add its decision to the memory log, as ``propagate()`` does."""
    recorder = getattr(graph, "record_decision", None)
    if not callable(recorder) or not final_state.get("final_trade_decision"):
        return
    try:
        recorder(ticker, analysis_date, final_state)
    except Exception as exc:
        print(f"[fast_path] could not record the decision in the memory log: {exc}", flush=True)


def run_analysts_in_parallel(
    graph: Any,
    ticker: str,
    analysis_date: str,
    asset_type: str,
    selected_analysts: tuple[str, ...],
    import_module: Callable[[str], Any],
    extract_reports: Callable[[Any], dict[str, str]],
    on_update: Optional[Callable[[str, Any], None]] = None,
    stages: Any = None,
    on_stage: Optional[Callable[[str, str], None]] = None,
) -> tuple[dict[str, Any], Any]:
    """Run the selected analysts concurrently, then the rest of the pipeline.

    Returns ``(final_state, rating)`` exactly like ``graph.propagate``.
    """

    with _run_scope(graph, import_module):
        return _run_analysts_in_parallel(
            graph, ticker, analysis_date, asset_type, selected_analysts,
            import_module, extract_reports, on_update, stages, on_stage,
        )


def _run_analysts_in_parallel(
    graph: Any,
    ticker: str,
    analysis_date: str,
    asset_type: str,
    selected_analysts: tuple[str, ...],
    import_module: Callable[[str], Any],
    extract_reports: Callable[[Any], dict[str, str]],
    on_update: Optional[Callable[[str, Any], None]],
    stages: Any,
    on_stage: Optional[Callable[[str, str], None]],
) -> tuple[dict[str, Any], Any]:
    analyst_execution = import_module("tradingagents.graph.analyst_execution")
    agents = import_module("tradingagents.agents")

    plan = analyst_execution.build_analyst_execution_plan(selected_analysts)

    factories = {
        "market": lambda: agents.create_market_analyst(graph.quick_thinking_llm),
        "social": lambda: agents.create_sentiment_analyst(graph.quick_thinking_llm),
        "news": lambda: agents.create_news_analyst(graph.quick_thinking_llm),
        "fundamentals": lambda: agents.create_fundamentals_analyst(graph.quick_thinking_llm),
    }

    base_state = create_run_state_compat(graph, ticker, analysis_date, asset_type)
    graph_args = graph.propagator.get_graph_args()
    invoke_config = graph_args.get("config", {})

    branches = {
        spec.key: _build_analyst_branch(graph, spec, factories[spec.key](), import_module)
        for spec in plan.specs
    }

    def run_branch(spec):
        started = time.monotonic()
        if on_stage:
            on_stage(spec.key, "started")
        try:
            state = branches[spec.key].invoke(dict(base_state), config=invoke_config)
        except Exception:
            if on_stage:
                on_stage(spec.key, "error")
            raise
        if on_stage:
            on_stage(spec.key, "completed")
        duration = time.monotonic() - started
        print(f"[fast_path] {spec.agent_node} finished in {duration:.1f}s", flush=True)
        return spec, state

    # The engine's Memory Log step settles past decisions and loads the lessons
    # the Portfolio Manager reads; it runs beside the analysts, as in the engine.
    memory_step = getattr(graph, "_memory_step", None)

    def run_memory():
        try:
            return memory_step(dict(base_state)) or {}
        except Exception as exc:
            print(f"[fast_path] memory log step failed: {exc}", flush=True)
            return {}

    merged: dict[str, Any] = dict(base_state)
    errors: list[str] = []

    workers = len(plan.specs) + (1 if callable(memory_step) else 0)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        memory_future = _in_context(pool, run_memory) if callable(memory_step) else None
        futures = [_in_context(pool, run_branch, spec) for spec in plan.specs]
        for future in futures:
            spec, state = future.result()
            report = state.get(spec.report_key) if isinstance(state, dict) else None
            if report:
                merged[spec.report_key] = report
                if on_update:
                    try:
                        on_update(spec.report_key, report)
                    except Exception as exc:
                        errors.append(f"{spec.report_key} publish failed: {exc}")
        if memory_future is not None:
            merged.update(memory_future.result())

    for key in ("market_report", "sentiment_report", "news_report", "fundamentals_report"):
        merged.setdefault(key, "")
        if merged[key] is None:
            merged[key] = ""

    # Downstream agents read the report fields, not the analyst chatter.
    merged["messages"] = [("human", ticker)]

    enabled = normalize_stages(stages)
    if enabled is not None:
        skipped = [stage for stage in TAIL_STAGES if stage not in enabled]
        if skipped:
            print(f"[fast_path] skipping stages: {', '.join(skipped)}", flush=True)
    tail = _build_tail_graph(graph, import_module, enabled, on_stage)
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

    # Only a full pipeline produces a decision worth learning from.
    if enabled is None or set(TAIL_STAGES) <= enabled:
        record_run(graph, ticker, analysis_date, final_state)

    return final_state, decision_rating(final_state, import_module)


def create_initial_state_compat(propagator, ticker, analysis_date, **extra):
    """Call propagator.create_initial_state, dropping keyword arguments the
    installed TradingAgents version does not accept (e.g. asset_type)."""
    import inspect
    fn = propagator.create_initial_state
    try:
        params = inspect.signature(fn).parameters
        if not any(p.kind == p.VAR_KEYWORD for p in params.values()):
            extra = {k: v for k, v in extra.items() if k in params}
    except (TypeError, ValueError):
        pass
    try:
        return fn(ticker, analysis_date, **extra)
    except TypeError:
        return fn(ticker, analysis_date)
