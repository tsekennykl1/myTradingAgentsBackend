"""Checks against the real TradingAgents package (skipped when it is not installed).

The backend pins one TradingAgents release in requirements.txt and in
scripts/bootstrap.sh. These tests make sure both pins agree with the installed
package, that every module and attribute app/fast_path.py relies on exists in
it, and that a full fast-path run works end to end on the real graph with
stand-in agents (no LLM or network calls).
"""

from __future__ import annotations

import importlib
import re
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import fast_path, tradingagents_service  # noqa: E402

PINNED_VERSION = "0.7.1"


def _requirements_pin() -> str:
    text = (ROOT / "requirements.txt").read_text()
    match = re.search(r"TauricResearch/TradingAgents/archive/refs/tags/v([\d.]+)\.tar\.gz", text)
    assert match, "requirements.txt has no pinned TradingAgents tag"
    return match.group(1)


def _bootstrap_pin() -> str:
    text = (ROOT / "scripts" / "bootstrap.sh").read_text()
    match = re.search(r'TA_TAG="\$\{TA_TAG:-v([\d.]+)\}"', text)
    assert match, "scripts/bootstrap.sh has no default TA_TAG"
    return match.group(1)


def test_requirements_and_bootstrap_pin_tradingagents_071():
    assert _requirements_pin() == PINNED_VERSION
    assert _bootstrap_pin() == PINNED_VERSION


def test_rating_from_propagate_becomes_the_lowercase_action():
    result = tradingagents_service._normalize_propagate_result(
        {"final_trade_decision": "**Rating**: Overweight\nAdd on dips."}, "Overweight", ("market",)
    )
    assert result["decision_structured"]["action"] == "overweight"


def test_review_rating_is_reported_as_review():
    result = tradingagents_service._normalize_propagate_result({}, "REVIEW", ("market",))
    assert result["decision_structured"]["action"] == "review"


def test_recursion_limit_stays_above_the_tool_round_cap():
    pytest.importorskip("tradingagents")
    config = tradingagents_service._build_tradingagents_config({"provider": "openai"})
    assert config["max_recur_limit"] > 2 * config["max_tool_rounds"] + 2


# --------------------------------------------------------------------------
# Real package
# --------------------------------------------------------------------------

def test_installed_version_matches_the_pin():
    tradingagents = pytest.importorskip("tradingagents")
    assert tradingagents.__version__ == PINNED_VERSION


def test_every_engine_symbol_the_fast_path_uses_exists():
    pytest.importorskip("tradingagents")
    setup = importlib.import_module("tradingagents.graph.setup")
    assert callable(setup._analyst_graph)
    assert set(setup.DEBATE_PATH_MAP) >= {"Bull Researcher", "Bear Researcher", "Research Manager"}

    plan = importlib.import_module("tradingagents.graph.analyst_execution").build_analyst_execution_plan(
        ["market", "social", "news", "fundamentals"]
    )
    assert [spec.report_key for spec in plan.specs] == [
        "market_report", "sentiment_report", "news_report", "fundamentals_report",
    ]

    agents = importlib.import_module("tradingagents.agents")
    for name in (
        "create_market_analyst", "create_sentiment_analyst", "create_news_analyst",
        "create_fundamentals_analyst", "create_bull_researcher", "create_bear_researcher",
        "create_research_manager", "create_trader", "create_aggressive_debator",
        "create_conservative_debator", "create_neutral_debator", "create_portfolio_manager",
    ):
        assert callable(getattr(agents, name)), name

    assert "AgentState" in dir(importlib.import_module("tradingagents.agents.state"))
    assert callable(importlib.import_module("tradingagents.dataflows.router").route_to_vendor)
    assert callable(importlib.import_module("tradingagents.agents.tools").route_to_vendor)
    assert callable(importlib.import_module("tradingagents.dataflows.config").run_config)
    assert callable(importlib.import_module("tradingagents.agents.rating").run_rating)

    graph_cls = importlib.import_module("tradingagents.graph.trading_graph").TradingAgentsGraph
    for name in ("create_run_state", "_memory_step", "record_decision", "stream_run", "propagate"):
        assert callable(getattr(graph_cls, name)), name


def _real_graph(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-used")
    config = tradingagents_service._build_tradingagents_config(
        {"provider": "openai", "quickModel": "gpt-6-luna", "deepModel": "gpt-6-sol"}
    )
    config.update(
        results_dir=str(tmp_path / "results"),
        data_cache_dir=str(tmp_path / "cache"),
        memory_log_path=str(tmp_path / "memory" / "trading_memory.md"),
    )
    graph_cls = importlib.import_module("tradingagents.graph.trading_graph").TradingAgentsGraph
    graph = graph_cls(selected_analysts=("market", "news"), debug=False, config=config)
    # Identity lookup goes to Yahoo; keep the test offline.
    graph.resolve_instrument_context = lambda *args, **kwargs: ""
    return graph, config


def _stand_in_agents():
    from langchain_core.messages import AIMessage

    real = importlib.import_module("tradingagents.agents")

    def analyst(report_key):
        return lambda llm: (lambda state: {
            "messages": [AIMessage(content="done")],
            report_key: f"{report_key} for {state['company_of_interest']}",
        })

    def researcher(side):
        def factory(llm):
            def node(state):
                debate = dict(state["investment_debate_state"])
                argument = f"{side} Analyst: case"
                debate[f"{side.lower()}_history"] = argument
                debate["history"] = (debate.get("history") or "") + "\n" + argument
                debate["current_response"] = argument
                debate["count"] = debate["count"] + 1
                return {"investment_debate_state": debate}
            return node
        return factory

    def debator(name):
        return lambda llm: (lambda state: {"risk_debate_state": {
            **state["risk_debate_state"], f"current_{name}_response": f"{name.title()} Analyst: view",
        }})

    def portfolio_manager(llm):
        return lambda state: {
            "final_trade_decision": "**Rating**: Buy\nBuild the position over two weeks.",
            "final_rating": "Buy",
        }

    return types.SimpleNamespace(
        AgentState=real.AgentState,
        create_market_analyst=analyst("market_report"),
        create_sentiment_analyst=analyst("sentiment_report"),
        create_news_analyst=analyst("news_report"),
        create_fundamentals_analyst=analyst("fundamentals_report"),
        create_bull_researcher=researcher("Bull"),
        create_bear_researcher=researcher("Bear"),
        create_research_manager=lambda llm: (lambda state: {"investment_plan": "plan"}),
        create_trader=lambda llm: (lambda state: {"trader_investment_plan": "trade"}),
        create_aggressive_debator=debator("aggressive"),
        create_conservative_debator=debator("conservative"),
        create_neutral_debator=debator("neutral"),
        create_portfolio_manager=portfolio_manager,
    )


def test_fast_path_runs_end_to_end_on_the_real_graph(tmp_path, monkeypatch):
    pytest.importorskip("tradingagents")
    graph, config = _real_graph(tmp_path, monkeypatch)
    stand_ins = _stand_in_agents()

    def import_module(name):
        return stand_ins if name == "tradingagents.agents" else importlib.import_module(name)

    events: list[tuple[str, str]] = []
    final_state, rating = fast_path.run_analysts_in_parallel(
        graph,
        "AAPL",
        "2026-10-30",
        "stock",
        ("market", "news"),
        import_module,
        tradingagents_service._extract_reports_from_state,
        None,
        None,
        lambda stage, event: events.append((stage, event)),
    )

    assert rating == "Buy"
    assert final_state["market_report"] == "market_report for AAPL"
    assert final_state["news_report"] == "news_report for AAPL"
    assert final_state["trader_investment_plan"] == "trade"
    assert final_state["risk_debate_state"]["count"] == 3
    assert ("portfolio_manager", "completed") in events
    # Recorded in the memory log, as propagate() would.
    memory_log = Path(config["memory_log_path"]).read_text()
    assert "AAPL" in memory_log and "2026-10-30" in memory_log

    normalized = tradingagents_service._normalize_propagate_result(final_state, rating, ("market", "news"))
    assert normalized["decision_structured"]["action"] == "buy"
