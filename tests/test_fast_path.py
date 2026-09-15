"""Structural tests for the parallel analyst fast path.

The real TradingAgents package is not installed in CI, so the graph pieces are
stubbed with the same shapes the engine exposes. LangGraph itself is real, so
the node/edge wiring is genuinely exercised.
"""

from __future__ import annotations

import sys
import time
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, TypedDict

import langgraph.graph as lg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import fast_path  # noqa: E402


def _reducer(left, right):
    return right


class State(TypedDict, total=False):
    messages: Annotated[list, _reducer]
    company_of_interest: str
    market_report: str
    sentiment_report: str
    news_report: str
    fundamentals_report: str
    investment_plan: str
    trader_investment_plan: str
    investment_debate_state: dict
    risk_debate_state: dict
    final_trade_decision: str


@dataclass(frozen=True)
class Spec:
    key: str
    agent_node: str
    clear_node: str
    tool_node: str
    report_key: str


SPECS = {
    "market": Spec("market", "Market Analyst", "Msg Clear Market", "tools_market", "market_report"),
    "social": Spec("social", "Sentiment Analyst", "Msg Clear Sentiment", "tools_social", "sentiment_report"),
    "news": Spec("news", "News Analyst", "Msg Clear News", "tools_news", "news_report"),
    "fundamentals": Spec(
        "fundamentals", "Fundamentals Analyst", "Msg Clear Fundamentals", "tools_fundamentals", "fundamentals_report"
    ),
}


class Plan:
    def __init__(self, keys):
        self.specs = [SPECS[k] for k in keys]


class ConditionalLogic:
    def __getattr__(self, name):
        if name.startswith("should_continue_") and name.split("_")[-1] in SPECS:
            key = name.rsplit("_", 1)[-1]
            return lambda state, key=key: SPECS[key].clear_node
        raise AttributeError(name)

    def should_continue_debate(self, state):
        return "Research Manager"

    def should_continue_risk_analysis(self, state):
        return "Portfolio Manager"


class Propagator:
    def create_initial_state(self, ticker, date, asset_type="stock", instrument_context=""):
        return {
            "messages": [("human", ticker)],
            "company_of_interest": ticker,
            "market_report": "",
            "sentiment_report": "",
            "news_report": "",
            "fundamentals_report": "",
            "investment_debate_state": {"count": 0},
            "risk_debate_state": {"count": 0},
        }

    def get_graph_args(self):
        return {"stream_mode": "values", "config": {"recursion_limit": 25}}


def _analyst(report_key):
    def node(state):
        time.sleep(0.2)  # long enough that sequential execution would be visible
        return {report_key: f"{report_key} for {state['company_of_interest']}"}

    return node


class FakeGraph:
    quick_thinking_llm = "quick"
    deep_thinking_llm = "deep"

    def __init__(self):
        self.conditional_logic = ConditionalLogic()
        self.propagator = Propagator()
        self.tool_nodes = {key: (lambda state: {}) for key in SPECS}


def _modules():
    agents = types.SimpleNamespace(
        create_market_analyst=lambda llm: _analyst("market_report"),
        create_sentiment_analyst=lambda llm: _analyst("sentiment_report"),
        create_news_analyst=lambda llm: _analyst("news_report"),
        create_fundamentals_analyst=lambda llm: _analyst("fundamentals_report"),
        create_bull_researcher=lambda llm: (lambda s: {"investment_debate_state": {"count": 1}}),
        create_bear_researcher=lambda llm: (lambda s: {"investment_debate_state": {"count": 2}}),
        create_research_manager=lambda llm: (lambda s: {"investment_plan": "plan"}),
        create_trader=lambda llm: (lambda s: {"trader_investment_plan": "trade"}),
        create_aggressive_debator=lambda llm: (lambda s: {"risk_debate_state": {"count": 1}}),
        create_conservative_debator=lambda llm: (lambda s: {"risk_debate_state": {"count": 2}}),
        create_neutral_debator=lambda llm: (lambda s: {"risk_debate_state": {"count": 3}}),
        create_portfolio_manager=lambda llm: (lambda s: {"final_trade_decision": "BUY"}),
    )
    return {
        "langgraph.graph": lg,
        "tradingagents.agents": agents,
        "tradingagents.agents.utils.agent_states": types.SimpleNamespace(AgentState=State),
        "tradingagents.agents.utils.agent_utils": types.SimpleNamespace(create_msg_delete=lambda: (lambda s: {})),
        "tradingagents.graph.analyst_execution": types.SimpleNamespace(
            build_analyst_execution_plan=lambda keys: Plan(keys)
        ),
        "tradingagents.graph.setup": types.SimpleNamespace(
            DEBATE_PATH_MAP={
                "Bull Researcher": "Bull Researcher",
                "Bear Researcher": "Bear Researcher",
                "Research Manager": "Research Manager",
            },
            RISK_ANALYSIS_PATH_MAP={
                "Aggressive Analyst": "Aggressive Analyst",
                "Conservative Analyst": "Conservative Analyst",
                "Neutral Analyst": "Neutral Analyst",
                "Portfolio Manager": "Portfolio Manager",
            },
        ),
    }


def _import_module(name):
    return _modules()[name]


def _extract_reports(chunk):
    return {k: v for k, v in chunk.items() if isinstance(v, str) and k.endswith(("report", "plan", "decision"))}


def test_parallel_analysts_produce_all_reports_and_a_decision():
    published: dict[str, Any] = {}
    started = time.monotonic()

    final_state, decision = fast_path.run_analysts_in_parallel(
        FakeGraph(),
        "AAPL",
        "2026-09-15",
        "stock",
        ("market", "social", "news", "fundamentals"),
        _import_module,
        _extract_reports,
        lambda name, content: published.__setitem__(name, content),
    )
    elapsed = time.monotonic() - started

    assert decision == "BUY"
    for key in ("market_report", "sentiment_report", "news_report", "fundamentals_report"):
        assert final_state[key], f"{key} missing"
        assert key in published
    # Four 0.2s analysts sequentially would take >=0.8s; concurrently well under.
    assert elapsed < 0.6, f"analysts did not run concurrently (took {elapsed:.2f}s)"


def test_subset_of_analysts_leaves_other_reports_empty():
    final_state, decision = fast_path.run_analysts_in_parallel(
        FakeGraph(),
        "0700.HK",
        "2026-09-15",
        "stock",
        ("market",),
        _import_module,
        _extract_reports,
        None,
    )
    assert final_state["market_report"]
    assert final_state["news_report"] == ""
    assert decision == "BUY"


def test_vendor_cache_memoizes_by_arguments():
    calls: list[tuple] = []

    interface = types.SimpleNamespace(
        route_to_vendor=lambda name, *a, **k: (calls.append((name, a)), f"data:{name}")[1]
    )
    fast_path._CACHE.clear()
    fast_path._CACHE_INSTALLED = False

    assert fast_path.install_vendor_cache(lambda _name: interface)

    assert interface.route_to_vendor("get_stock_data", "AAPL", "2026-01-01", "2026-09-15") == "data:get_stock_data"
    assert interface.route_to_vendor("get_stock_data", "AAPL", "2026-01-01", "2026-09-15") == "data:get_stock_data"
    interface.route_to_vendor("get_stock_data", "MSFT", "2026-01-01", "2026-09-15")

    assert len(calls) == 2, calls

    fast_path._CACHE.clear()
    fast_path._CACHE_INSTALLED = False


def test_skipped_stages_are_not_executed():
    final_state, decision = fast_path.run_analysts_in_parallel(
        FakeGraph(),
        "AAPL",
        "2026-09-15",
        "stock",
        ("market",),
        _import_module,
        _extract_reports,
        None,
        ["market", "research_manager", "trader", "portfolio_manager"],
    )
    # Debates were opted out of, so their state never advances.
    assert final_state["investment_debate_state"] == {"count": 0}
    assert final_state["risk_debate_state"] == {"count": 0}
    # The stages that were kept still ran.
    assert final_state["investment_plan"] == "plan"
    assert final_state["trader_investment_plan"] == "trade"
    assert decision == "BUY"


def test_all_stages_run_when_none_are_specified():
    final_state, _ = fast_path.run_analysts_in_parallel(
        FakeGraph(),
        "AAPL",
        "2026-09-15",
        "stock",
        ("market",),
        _import_module,
        _extract_reports,
        None,
        None,
    )
    assert final_state["investment_debate_state"]["count"] > 0
    assert final_state["risk_debate_state"]["count"] > 0


def test_analyst_only_stage_list_skips_the_tail():
    assert fast_path.normalize_stages(["market", "news"]) == set()
    assert fast_path.normalize_stages([]) == set()
    assert fast_path.normalize_stages(["Trader"]) == {"trader"}
