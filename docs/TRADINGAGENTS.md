# The TradingAgents framework, and how this backend uses it

Upstream project: <https://github.com/TauricResearch/TradingAgents> — Yijia Xiao,
Edward Sun, Di Luo, Wei Wang (Tauric Research, 2024),
[arXiv:2412.20138](https://arxiv.org/abs/2412.20138). It is vendored here as the
`TradingAgents/` submodule and is **never modified**.

## The idea

Instead of asking one model "should I buy?", the framework imitates a trading
desk: specialised LLM agents each write a report, argue with each other, and a
portfolio manager makes the final call. Every step leaves a written artifact, so
the decision is auditable rather than a black box.

## The agents, in running order

| Stage id | Agents | Produces |
| --- | --- | --- |
| `market` | Market Analyst | `market_report` — price action, trend, indicators |
| `social` | Sentiment Analyst | `sentiment_report` — social/retail mood |
| `news` | News Analyst | `news_report` — headlines and macro events |
| `fundamentals` | Fundamentals Analyst | `fundamentals_report` — financials, valuation |
| `research_debate` | Bull Researcher vs Bear Researcher | `bull_report`, `bear_report` |
| `research_manager` | Research Manager | `investment_plan` — settles the debate |
| `trader` | Trader | `trader_plan` — concrete entry, sizing, exits |
| `risk_debate` | Aggressive, Conservative, Neutral analysts | `risk_aggressive`, `risk_conservative`, `risk_neutral` |
| `portfolio_manager` | Portfolio Manager | `final_trade_decision` — the verdict |

The analyst stages are independent, so they can run at the same time. The later
stages are a chain: each one reads what came before. `research_depth`
(Shallow / Medium / Deep) controls how many debate rounds happen.

## How this backend calls it

`app/tradingagents_service.py` is the only file that touches the framework, and it
is written defensively because upstream's API changes between versions:

1. `_ensure_local_repo_on_syspath()` makes the submodule importable.
2. `_resolve_graph_class()` / `_resolve_callable()` look for `TradingAgentsGraph`
   or an equivalent entry point, whichever this version exposes.
3. `normalize_engine_config()` translates the dashboard's request into the
   engine's config: provider, quick/deep model, selected analysts, debate rounds,
   language, cache directory.
4. `_filter_kwargs_for_signature()` passes only the arguments the installed
   version accepts, so an extra field never raises `TypeError`.
5. `_coerce_result_to_dict()` accepts a dict, a dataclass, or a `(state, decision)`
   tuple and flattens it.
6. If the framework is not installed at all, `_build_mock_result()` returns a
   clearly-labelled placeholder so the dashboard remains usable.

## The fast path

`app/fast_path.py` is our optional accelerator (opt out with environment flags):

- **Parallel analysts** — the four analyst branches run in a thread pool instead
  of sequentially, then the graph continues as normal.
- **Parallel risk debate** — Aggressive, Conservative and Neutral each see the
  same immutable snapshot of the prior round, run concurrently, and are merged in
  a fixed order so the transcript is deterministic.
- **Stage selection** — `normalize_stages()` turns the request's `stages` list
  into the set to execute; unselected stages become pass-through nodes.
- **Vendor data cache** — repeated market/news lookups for the same ticker and
  date inside one run are served from memory.

Tuning lives in `.env`: `TRADINGAGENTS_PARALLEL_ANALYSTS`,
`TRADINGAGENTS_DATA_CACHE`, `TRADINGAGENTS_DATA_CACHE_TTL`.

## Cost and honesty notes

- Every stage is one or more LLM calls. Deselecting stages is the most effective
  way to cut cost and time.
- The agents reason over the data they are given; they do not execute trades and
  their output is not investment advice.
- Reports are published as they finish, so a cancelled or failed run still leaves
  whatever was already written.
