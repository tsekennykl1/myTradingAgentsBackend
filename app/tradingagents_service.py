"""Adapter between this backend and the vendored TradingAgents framework.

The TradingAgents/ directory is upstream research code (Tauric Research, 2024 -
arXiv 2412.20138) and is never modified. Its public API has changed between
versions, so this file deliberately discovers it at runtime instead of importing
a fixed symbol:

  _resolve_graph_class()/_resolve_callable()  find TradingAgentsGraph or an
      equivalent entry point in the installed package
  _filter_kwargs_for_signature()              pass only the arguments that this
      version actually accepts
  normalize_engine_config()                   translate the dashboard's request
      (provider, models, analysts, depth, language) into the engine's config
  _coerce_result_to_dict()                    accept whatever shape it returns
  _build_mock_result()                        stand-in output when the engine is
      not installed, so the dashboard stays usable

When app/fast_path.py says parallel mode is enabled, execution is handed to
fast_path.run_analysts_in_parallel() instead of the engine's sequential graph.
"""

from __future__ import annotations

import importlib
import inspect
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Callable, Optional

from app import fast_path


BASE_DIR = Path(__file__).resolve().parent.parent
# The framework is normally installed with pip (see requirements.txt:
# tradingagents @ git+https://github.com/TauricResearch/TradingAgents.git).
# A checkout next to this backend is only an optional fallback, kept so an
# existing clone keeps working; set TRADINGAGENTS_LOCAL_DIR to point elsewhere.
LOCAL_TRADINGAGENTS_REPO_DIR = Path(
    os.getenv("TRADINGAGENTS_LOCAL_DIR") or (BASE_DIR / "TradingAgents")
)


def _ensure_local_repo_on_syspath() -> None:
    """Add a local TradingAgents checkout to sys.path if one exists.

    No-op when the framework is pip-installed, which is the default.
    """
    if not LOCAL_TRADINGAGENTS_REPO_DIR.exists():
        return

    repo_path = str(LOCAL_TRADINGAGENTS_REPO_DIR)
    if repo_path not in sys.path:
        sys.path.insert(0, repo_path)


def _import_module(module_name: str):
    _ensure_local_repo_on_syspath()
    return importlib.import_module(module_name)


def _has_parameter(sig: inspect.Signature, name: str) -> bool:
    return name in sig.parameters


def _accepts_kwargs(sig: inspect.Signature) -> bool:
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())


def _filter_kwargs_for_signature(fn: Callable[..., Any], values: dict[str, Any]) -> dict[str, Any]:
    try:
        sig = inspect.signature(fn)
    except Exception:
        return values

    if _accepts_kwargs(sig):
        return values

    return {k: v for k, v in values.items() if _has_parameter(sig, k)}


def _coerce_result_to_dict(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        return result

    if hasattr(result, "model_dump"):
        try:
            dumped = result.model_dump()
            if isinstance(dumped, dict):
                return dumped
            return {"output": dumped}
        except Exception:
            pass

    if hasattr(result, "dict"):
        try:
            dumped = result.dict()
            if isinstance(dumped, dict):
                return dumped
            return {"output": dumped}
        except Exception:
            pass

    if isinstance(result, tuple):
        return {"output": list(result)}

    return {"output": str(result)}


def _resolve_graph_class() -> tuple[str, str, Any]:
    candidates: list[tuple[str, str]] = [
        ("tradingagents.graph.trading_graph", "TradingAgentsGraph"),
        ("tradingagents.graph", "TradingAgentsGraph"),
        ("graph.trading_graph", "TradingAgentsGraph"),
        ("graph", "TradingAgentsGraph"),
    ]

    tried: list[str] = []

    for module_name, attr_name in candidates:
        try:
            module = _import_module(module_name)
        except Exception as exc:
            tried.append(f"{module_name}: import failed: {exc}")
            continue

        target = getattr(module, attr_name, None)
        if target is None:
            tried.append(f"{module_name}.{attr_name}: not found")
            continue

        if inspect.isclass(target):
            return module_name, attr_name, target

        tried.append(f"{module_name}.{attr_name}: found but not class")

    raise RuntimeError(
        "TradingAgents graph class not found. "
        f"Tried: {' | '.join(tried)}"
    )


def _resolve_callable() -> tuple[str, str, Callable[..., Any]]:
    candidates: list[tuple[str, str]] = [
        ("tradingagents", "run"),
        ("tradingagents", "analyze"),
        ("tradingagents", "run_tradingagents"),
        ("tradingagents.graph.trading_graph", "run"),
        ("tradingagents.graph.trading_graph", "analyze"),
        ("cli.main", "run_analysis"),
        ("cli.main", "analyze"),
    ]

    tried: list[str] = []

    for module_name, attr_name in candidates:
        try:
            module = _import_module(module_name)
        except Exception as exc:
            tried.append(f"{module_name}: import failed: {exc}")
            continue

        target = getattr(module, attr_name, None)
        if target is None:
            tried.append(f"{module_name}.{attr_name}: not found")
            continue

        if callable(target):
            return module_name, attr_name, target

        tried.append(f"{module_name}.{attr_name}: found but not callable")

    raise RuntimeError(
        "TradingAgents callable entrypoint not found. "
        f"Tried: {' | '.join(tried)}"
    )


def _resolve_callable_or_class() -> tuple[str, str, Any, str]:
    forced_target = os.getenv("TRADINGAGENTS_TARGET", "").strip()
    if forced_target:
        try:
            module_name, attr_name, target_type = forced_target.split(":")
            module = _import_module(module_name)
            target = getattr(module, attr_name)
            return module_name, attr_name, target, target_type
        except Exception as exc:
            raise RuntimeError(
                f"Invalid TRADINGAGENTS_TARGET='{forced_target}': {exc}"
            ) from exc

    graph_errors: list[str] = []
    try:
        module_name, attr_name, cls = _resolve_graph_class()
        return module_name, attr_name, cls, "class"
    except Exception as exc:
        graph_errors.append(str(exc))

    try:
        module_name, attr_name, fn = _resolve_callable()
        return module_name, attr_name, fn, "callable"
    except Exception as exc:
        graph_errors.append(str(exc))

    raise RuntimeError(" | ".join(graph_errors))


def engine_is_available() -> bool:
    try:
        _resolve_callable_or_class()
        return True
    except Exception:
        return False


def normalize_engine_config(payload: dict[str, Any]) -> dict[str, Any]:
    ticker = str(payload.get("ticker", "")).strip().upper()
    if not ticker:
        raise ValueError("ticker is required")

    analysis_date = payload.get("analysisDate") or payload.get("analysis_date")
    if analysis_date is not None:
        analysis_date = str(analysis_date).strip()

    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}

    provider = (
        payload.get("provider")
        or params.get("llm_provider")
        or "OpenAI"
    )
    quick_model = (
        payload.get("quickModel")
        or params.get("quick_think_llm")
        or ""
    )
    deep_model = (
        payload.get("deepModel")
        or params.get("deep_think_llm")
        or ""
    )
    research_depth = (
        payload.get("researchDepth")
        or params.get("research_depth")
        or "Shallow"
    )
    language = (
        payload.get("language")
        or params.get("language")
        or "English"
    )

    analysts = payload.get("analysts") or params.get("analysts") or [
        "Market Analyst",
        "Sentiment Analyst",
        "News Analyst",
        "Fundamentals Analyst",
    ]
    analysts = [str(x).strip() for x in analysts if str(x).strip()]
    if not analysts:
        analysts = [
            "Market Analyst",
            "Sentiment Analyst",
            "News Analyst",
            "Fundamentals Analyst",
        ]

    stages = payload.get("stages")
    if stages is None:
        stages = payload.get("selected_stages")
    if stages is None:
        stages = params.get("stages")
    if stages is None:
        stages = params.get("selected_stages")
    normalized_stages = None if stages is None else [str(x).strip() for x in stages if str(x).strip()]

    return {
        "ticker": ticker,
        "analysisDate": analysis_date,
        "provider": str(provider).strip() or "OpenAI",
        "quickModel": str(quick_model).strip(),
        "deepModel": str(deep_model).strip(),
        "researchDepth": str(research_depth).strip() or "Shallow",
        "analysts": analysts,
        "stages": normalized_stages,
        "language": str(language).strip() or "English",
        "params": params,
    }


def _build_mock_result(config: dict[str, Any]) -> dict[str, Any]:
    ticker = config["ticker"]

    return {
        "reports": {
            "market_report": (
                f"{ticker} is showing a mixed technical structure. "
                "Short-term price action should be assessed together with trend and volume."
            ),
            "sentiment_report": (
                f"Sentiment around {ticker} appears balanced with no overwhelmingly bullish "
                "or bearish catalyst in this fallback mode."
            ),
            "bull_report": (
                "- Trend resilience if macro backdrop stabilizes\n"
                "- Technical rebound potential near support\n"
                "- Upside if earnings or guidance surprises"
            ),
            "bear_report": (
                "- Valuation or sentiment compression risk\n"
                "- Weak momentum may continue\n"
                "- Macro uncertainty can pressure multiples"
            ),
            "investment_plan": (
                "Long-term investors may scale in gradually and review thesis against fundamentals."
            ),
            "trader_plan": (
                "Short-term traders may wait for confirmation above resistance or a reversal at support."
            ),
            "risk_aggressive": "Aggressive investors may use smaller tactical sizing with strict risk limits.",
            "risk_conservative": "Conservative investors may wait for stronger confirmation before entry.",
            "risk_neutral": "Neutral investors may consider phased entries and predefined exits.",
            "final_decision": (
                "HOLD\n"
                "Confidence: 55%\n"
                "Entry Price: monitor near current market levels only after confirmation.\n"
                "Stop Loss: use a disciplined technical stop.\n"
                "Overall view is balanced with moderate uncertainty."
            ),
        },
        "decision_structured": {
            "action": "Hold",
            "confidence": 55,
            "consensus": "Medium",
            "entryPrice": None,
            "stopLoss": None,
            "positionSizing": "Small to moderate sizing until trend confirmation.",
            "executiveSummary": (
                f"{ticker} currently presents a balanced risk/reward profile. "
                "Waiting for stronger confirmation may be prudent."
            ),
        },
        "stances": {a: "neutral" for a in config["analysts"]},
        "finished_agents": config["analysts"],
        "agent_notes": {a: "Completed analysis." for a in config["analysts"]},
        "stats": {
            "llmCalls": 0,
            "toolCalls": 0,
            "tokensIn": 0,
            "tokensOut": 0,
        },
    }


def _call_with_supported_signature(fn: Callable[..., Any], config: dict[str, Any]) -> Any:
    errors: list[str] = []

    try:
        sig = inspect.signature(fn)
    except Exception:
        sig = None

    if sig is not None:
        params = list(sig.parameters.values())

        if len(params) == 0:
            try:
                return fn()
            except Exception as exc:
                errors.append(f"fn(): {exc}")

        if len(params) == 1:
            try:
                return fn(config)
            except Exception as exc:
                errors.append(f"fn(config): {exc}")

    else:
        try:
            return fn(config)
        except Exception as exc:
            errors.append(f"fn(config): {exc}")

    try:
        kwargs = _filter_kwargs_for_signature(fn, config)
        return fn(**kwargs)
    except Exception as exc:
        errors.append(f"fn(**filtered_config): {exc}")

    explicit_kwargs = {
        "ticker": config.get("ticker"),
        "analysisDate": config.get("analysisDate"),
        "provider": config.get("provider"),
        "quickModel": config.get("quickModel"),
        "deepModel": config.get("deepModel"),
        "researchDepth": config.get("researchDepth"),
        "analysts": config.get("analysts"),
        "language": config.get("language"),
    }

    try:
        kwargs = _filter_kwargs_for_signature(fn, explicit_kwargs)
        return fn(**kwargs)
    except Exception as exc:
        errors.append(f"fn(**explicit_kwargs): {exc}")

    raise RuntimeError(" ; ".join(errors) if errors else "No supported call signature matched.")


def _invoke_callable(fn: Callable[..., Any], config: dict[str, Any]) -> dict[str, Any]:
    result = _call_with_supported_signature(fn, config)
    return _coerce_result_to_dict(result)


def _map_analysts_to_selected(analysts: list[str] | None) -> tuple[str, ...]:
    if not analysts:
        return ("market", "social", "news", "fundamentals")

    mapping = {
        "market analyst": "market",
        "technical analyst": "market",
        "market": "market",
        "sentiment analyst": "social",
        "social analyst": "social",
        "social": "social",
        "news analyst": "news",
        "news": "news",
        "fundamentals analyst": "fundamentals",
        "fundamental analyst": "fundamentals",
        "fundamentals": "fundamentals",
    }

    selected: list[str] = []
    for item in analysts:
        mapped = mapping.get(str(item).strip().lower())
        if mapped and mapped not in selected:
            selected.append(mapped)

    return tuple(selected or ["market", "social", "news", "fundamentals"])


def _coerce_max_debate_rounds(research_depth: str | None) -> int:
    value = str(research_depth or "").strip().lower()
    if value == "deep":
        return 3
    if value == "medium":
        return 2
    return 1


def _normalize_provider(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return "openai"

    mapping = {
        "openai": "openai",
        "deepseek": "deepseek",
        "anthropic": "anthropic",
        "google": "google",
        "xai": "xai",
        "groq": "groq",
        "ollama": "ollama",
        "azure": "azure",
        "openrouter": "openrouter",
        "qwen": "qwen",
        "glm": "glm",
        "minimax": "minimax",
    }
    return mapping.get(raw, raw)


def _build_tradingagents_config(config: dict[str, Any]) -> dict[str, Any]:
    default_config_module = _import_module("tradingagents.default_config")
    default_config = getattr(default_config_module, "DEFAULT_CONFIG").copy()

    params = config.get("params") if isinstance(config.get("params"), dict) else {}

    provider = params.get("llm_provider") or config.get("provider")
    quick_model = params.get("quick_think_llm") or config.get("quickModel")
    deep_model = params.get("deep_think_llm") or config.get("deepModel")
    research_depth = params.get("research_depth") or config.get("researchDepth")
    language = params.get("language") or config.get("language")

    default_config["llm_provider"] = _normalize_provider(provider)

    if quick_model:
        default_config["quick_think_llm"] = str(quick_model).strip()

    if deep_model:
        default_config["deep_think_llm"] = str(deep_model).strip()

    default_config["max_debate_rounds"] = _coerce_max_debate_rounds(research_depth)
    # Risk debate is 3 agents per round; one round is enough below "Deep".
    default_config["max_risk_discuss_rounds"] = (
        2 if str(research_depth or "").strip().lower() == "deep" else 1
    )

    if language:
        default_config["output_language"] = str(language).strip()

    return default_config


def _normalize_decision_payload(decision: Any) -> dict[str, Any]:
    if isinstance(decision, dict):
        return decision
    return {"raw": decision}


def _extract_reports_from_state(final_state: Any) -> dict[str, str]:
    reports: dict[str, str] = {}

    candidate_sources: list[Any] = []
    if isinstance(final_state, dict):
        candidate_sources.append(final_state)
    else:
        for attr in [
            "analyst_reports",
            "reports",
            "__dict__",
        ]:
            if hasattr(final_state, attr):
                candidate_sources.append(getattr(final_state, attr))

    for source in candidate_sources:
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            text = None
            if isinstance(value, str):
                text = value
            elif isinstance(value, dict):
                if isinstance(value.get("report"), str):
                    text = value["report"]
                elif isinstance(value.get("reasoning"), str):
                    text = value["reasoning"]
                elif isinstance(value.get("summary"), str):
                    text = value["summary"]
            if text:
                reports[str(key)] = text

    return reports


def _normalize_propagate_result(final_state: Any, decision: Any, selected_analysts: tuple[str, ...]) -> dict[str, Any]:
    decision_dict = _normalize_decision_payload(decision)
    reports = _extract_reports_from_state(final_state)

    stance_map = {
        "market": "neutral",
        "social": "neutral",
        "news": "neutral",
        "fundamentals": "neutral",
    }

    result: dict[str, Any] = {
        "reports": reports,
        "decision_structured": {
            "action": decision_dict.get("action") or decision_dict.get("rating"),
            "confidence": decision_dict.get("confidence"),
            "consensus": decision_dict.get("consensus"),
            "entryPrice": decision_dict.get("entryPrice") or decision_dict.get("entry_price"),
            "stopLoss": decision_dict.get("stopLoss") or decision_dict.get("stop_loss"),
            "positionSizing": decision_dict.get("positionSizing") or decision_dict.get("position_sizing"),
            "executiveSummary": decision_dict.get("executiveSummary") or decision_dict.get("reasoning"),
        },
        "stances": {name: stance_map.get(name, "neutral") for name in selected_analysts},
        "finished_agents": list(selected_analysts),
        "agent_notes": {name: "Completed analysis." for name in selected_analysts},
        "stats": {
            "llmCalls": None,
            "toolCalls": None,
            "tokensIn": None,
            "tokensOut": None,
        },
        "raw_result": {
            "final_state": _coerce_result_to_dict(final_state),
            "decision": decision_dict,
        },
    }

    if not result["reports"] and decision_dict.get("reasoning"):
        result["reports"] = {
            "final_decision": str(decision_dict["reasoning"])
        }

    if "action" not in result["decision_structured"] or result["decision_structured"]["action"] is None:
        if isinstance(decision_dict.get("signal"), str):
            result["decision_structured"]["action"] = decision_dict["signal"]

    return result


def _detect_asset_type(ticker: str) -> str:
    upper = ticker.upper()
    if upper.endswith("-USD") or upper.endswith("/USD"):
        return "crypto"
    return "stock"


def _invoke_graph_class(cls: Any, config: dict[str, Any], on_update: Optional[Callable[[str, Any], None]] = None, on_stage: Optional[Callable[[str, str], None]] = None) -> dict[str, Any]:
    ta_config = _build_tradingagents_config(config)
    selected_analysts = _map_analysts_to_selected(config.get("analysts"))

    ticker = str(config.get("ticker", "")).strip().upper()
    analysis_date = str(config.get("analysisDate") or "").strip()

    if not ticker:
        raise RuntimeError("ticker is required for TradingAgentsGraph.propagate().")
    if not analysis_date:
        raise RuntimeError("analysisDate is required for TradingAgentsGraph.propagate().")

    asset_type = _detect_asset_type(ticker)

    print(
        "[tradingagents_service] graph setup "
        f"ticker={ticker} "
        f"analysis_date={analysis_date} "
        f"asset_type={asset_type} "
        f"provider={ta_config.get('llm_provider')} "
        f"quick={ta_config.get('quick_think_llm')} "
        f"deep={ta_config.get('deep_think_llm')} "
        f"max_debate_rounds={ta_config.get('max_debate_rounds')} "
        f"selected_analysts={selected_analysts}",
        flush=True,
    )

    init_errors: list[str] = []
    graph = None

    constructor_patterns = [
        lambda: cls(selected_analysts=selected_analysts, debug=False, config=ta_config),
        lambda: cls(debug=False, config=ta_config),
        lambda: cls(config=ta_config),
        lambda: cls(),
    ]

    for idx, build in enumerate(constructor_patterns, start=1):
        try:
            graph = build()
            print(
                f"[tradingagents_service] TradingAgentsGraph constructor pattern #{idx} succeeded",
                flush=True,
            )
            break
        except Exception as exc:
            init_errors.append(str(exc))
            print(
                f"[tradingagents_service] TradingAgentsGraph constructor pattern #{idx} failed: {exc}",
                flush=True,
            )

    if graph is None:
        raise RuntimeError(
            "Could not instantiate TradingAgentsGraph: " + " ; ".join(init_errors)
        )

    propagate = getattr(graph, "propagate", None)
    if not callable(propagate):
        raise RuntimeError("TradingAgentsGraph has no callable propagate() method.")

    print(
        "[tradingagents_service] about to call propagate "
        f"ticker={ticker} analysis_date={analysis_date} asset_type={asset_type}",
        flush=True,
    )

    # Current TradingAgents exposes per-node deltas through graph.stream. Use it
    # when a publisher is supplied; otherwise preserve the stable propagate API.
    if fast_path.parallel_enabled():
        try:
            fast_path.install_vendor_cache(_import_module)
            final_state, decision = fast_path.run_analysts_in_parallel(
                graph,
                ticker,
                analysis_date,
                asset_type,
                selected_analysts,
                _import_module,
                _extract_reports_from_state,
                on_update,
                config.get("stages"),
                on_stage,
            )
            normalized = _normalize_propagate_result(final_state, decision, selected_analysts)
            normalized.setdefault("_engine_adapter", {})
            normalized["_engine_adapter"].update(
                {
                    "api": "fast_path.run_analysts_in_parallel",
                    "selected_analysts": list(selected_analysts),
                    "asset_type": asset_type,
                }
            )
            return normalized
        except Exception as exc:
            print(
                "[tradingagents_service] parallel analyst path failed, "
                f"falling back to sequential graph: {exc}",
                flush=True,
            )
            traceback.print_exc()

    stream_graph = getattr(graph, "graph", None)
    stream = getattr(stream_graph, "stream", None)
    propagator = getattr(graph, "propagator", None)
    if on_update and callable(stream) and propagator is not None:
        init_state = propagator.create_initial_state(ticker, analysis_date, asset_type=asset_type)
        args = propagator.get_graph_args()
        final_state: dict[str, Any] = {}
        for chunk in stream(init_state, **args):
            if not isinstance(chunk, dict):
                continue
            final_state.update(chunk)
            for report_name, content in _extract_reports_from_state(chunk).items():
                on_update(report_name, content)
        decision = final_state.get("final_trade_decision")
        result = (final_state, decision)
    else:
        try:
            result = propagate(ticker, analysis_date, asset_type=asset_type)
        except TypeError as exc:
            print(
                f"[tradingagents_service] propagate(..., asset_type=...) raised TypeError: {exc}; retrying without asset_type",
                flush=True,
            )
            result = propagate(ticker, analysis_date)

    print(
        f"[tradingagents_service] propagate returned type={type(result).__name__}",
        flush=True,
    )

    if isinstance(result, tuple) and len(result) == 2:
        final_state, decision = result
        print(
            "[tradingagents_service] propagate returned (final_state, decision)",
            flush=True,
        )
        normalized = _normalize_propagate_result(final_state, decision, selected_analysts)
        normalized.setdefault("_engine_adapter", {})
        normalized["_engine_adapter"].update(
            {
                "api": "TradingAgentsGraph.propagate",
                "selected_analysts": list(selected_analysts),
                "asset_type": asset_type,
            }
        )
        return normalized

    coerced = _coerce_result_to_dict(result)
    coerced.setdefault("_engine_adapter", {})
    coerced["_engine_adapter"].update(
        {
            "api": "TradingAgentsGraph.propagate",
            "selected_analysts": list(selected_analysts),
            "asset_type": asset_type,
        }
    )
    return coerced


def _call_tradingagents_package(config: dict[str, Any], on_update: Optional[Callable[[str, Any], None]] = None, on_stage: Optional[Callable[[str, str], None]] = None) -> dict[str, Any]:
    module_name, attr_name, target, target_type = _resolve_callable_or_class()

    print(
        f"[tradingagents_service] invoking {module_name}.{attr_name} ({target_type})",
        flush=True,
    )
    print(
        f"[tradingagents_service] config keys: {sorted(config.keys())}",
        flush=True,
    )

    try:
        if target_type == "callable":
            result = _invoke_callable(target, config)
        elif target_type == "class":
            result = _invoke_graph_class(target, config, on_update=on_update, on_stage=on_stage)
        else:
            raise RuntimeError(f"Unsupported target_type: {target_type}")

        result.setdefault("_engine_adapter", {})
        result["_engine_adapter"].update(
            {
                "module": module_name,
                "target": attr_name,
                "target_type": target_type,
            }
        )
        return result
    except Exception as exc:
        raise RuntimeError(
            f"TradingAgents target '{module_name}.{attr_name}' was found but invocation failed: {exc}"
        ) from exc


def run_tradingagents(config: dict[str, Any], on_update: Optional[Callable[[str, Any], None]] = None, on_stage: Optional[Callable[[str, str], None]] = None) -> dict[str, Any]:
    use_mock = os.getenv("TRADINGAGENTS_USE_MOCK", "0").lower() in {"1", "true", "yes"}
    if use_mock:
        result = _build_mock_result(config)
        if on_update:
            for name, content in result.get("reports", {}).items():
                on_update(name, content)
        return result

    try:
        print("[tradingagents_service] run_tradingagents start", flush=True)
        result = _call_tradingagents_package(config, on_update=on_update, on_stage=on_stage)
        print("[tradingagents_service] run_tradingagents finished successfully", flush=True)
        return result
    except Exception as exc:
        print(f"[tradingagents_service] run_tradingagents failed: {exc}", flush=True)
        fallback_on_error = os.getenv("TRADINGAGENTS_FALLBACK_TO_MOCK", "1").lower() in {"1", "true", "yes"}
        if fallback_on_error:
            mock = _build_mock_result(config)
            mock["engine_error"] = str(exc)
            mock["engine_traceback"] = traceback.format_exc()
            return mock
        raise RuntimeError(
            "TradingAgents engine is unavailable and fallback is disabled."
        ) from exc
