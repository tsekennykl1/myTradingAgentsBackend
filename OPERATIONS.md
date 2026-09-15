# Operations

Runs are persisted in SQLite and claimed by a bounded worker pool. Mount `data/` and `artifacts/` on persistent storage. Set `PUBLIC_BASE_URL` to the external HTTPS API origin and list allowed dashboards in `FRONTEND_ORIGINS`.

`GET /runs/{id}/status` is the polling contract. It returns readiness fields, a monotonic `version`, `poll_after_ms`, and an ETag. Send `If-None-Match`; an unchanged run returns 304 without a body.

Interrupted leases return to the queue at startup. Failed attempts use bounded retry controlled by `RUN_MAX_ATTEMPTS`. TradingAgents versions exposing `graph.stream()` publish reports progressively; older versions publish all reports after `propagate()` returns.

## Run speed tuning

Wall time is dominated by LLM turns. Three knobs, all on by default:

| Env var | Default | Effect |
| --- | --- | --- |
| `TRADINGAGENTS_PARALLEL_ANALYSTS` | `1` | Runs the selected analysts concurrently (each in its own single-analyst graph and message history) instead of chaining them. Set to `0` to restore the stock sequential graph. |
| `TRADINGAGENTS_DATA_CACHE` | `1` | Memoizes `dataflows.interface.route_to_vendor`, so repeat calls for the same ticker+date skip the network. |
| `TRADINGAGENTS_DATA_CACHE_TTL` | `21600` | Cache lifetime in seconds (6h). |

Debate rounds are derived from research depth: Shallow/Medium use one research
round, Deep uses three; risk discussion is one round below Deep and two on Deep.

If the installed TradingAgents package drifts structurally, the parallel path
logs the failure and falls back to the sequential graph automatically — runs
never fail because of this optimization.

### Stage opt-in / opt-out

The frontend's New Analysis form sends the stages the user ticked. The run
payload may carry `stages` (or `selected_stages`), e.g.

```json
{"stages": ["market", "research_debate", "research_manager", "trader", "risk_debate", "portfolio_manager"]}
```

Recognised tail stages: `research_debate` (Bull + Bear Researcher),
`research_manager`, `trader`, `risk_debate` (Aggressive + Conservative +
Neutral Analyst), `portfolio_manager`. Analyst stages (`market`, `social`,
`news`, `fundamentals`) continue to be controlled by `analysts` /
`selected_analysts`.

Stages left out of the list are skipped entirely — no LLM calls, so run time
drops roughly in proportion to what was dropped. Omitting `stages`, sending an
empty list, or sending only analyst stages keeps the full pipeline, so older
clients behave exactly as before. Skipping only applies to the parallel fast
path (`TRADINGAGENTS_PARALLEL_ANALYSTS=1`, the default); the sequential
fallback always runs the full graph.
