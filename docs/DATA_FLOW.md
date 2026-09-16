# One analysis, end to end

## Step 0 — the request

```http
POST /runs
{
  "ticker": "700",
  "analysis_date": "2026-09-15",
  "provider": "DeepSeek",
  "quick_model": "deepseek-chat",
  "deep_model": "deepseek-reasoner",
  "research_depth": "Medium",
  "selected_analysts": ["market"],
  "stages": ["market", "research_debate", "research_manager",
             "trader", "risk_debate", "portfolio_manager"],
  "language": "English"
}
```

`stages` is an opt-in list. A stage that is absent is genuinely skipped — the
graph replaces it with a pass-through node, and its reports never appear.

## Step 1 — accept and queue  (`create_or_reuse_run`)

1. Normalise ticker, date, provider and model names.
2. Validate that the provider's API key exists in `.env`.
3. Hash the normalised payload. An identical, still-active run is returned as-is
   (`reused: true`) instead of starting a second one.
4. Insert the row with status `queued`; respond `202 {run_id, reused, run}`.

## Step 2 — claim  (`app/worker.py` -> `claim_next_run`)

A worker thread takes the oldest queued row, stamps it with its worker id and a
lease expiry, and flips the status to `running`.

## Step 3 — market data and early artifacts

`download_price_frame()` fetches OHLCV history and `compute_*` adds RSI, MACD,
ATR, Bollinger, stochastic, OBV and VWAP. `market-data.json` and
`price-chart.json/.png/.html` are written immediately, and the run's partial
result is updated with the latest price, change, RSI and EMAs.

**This is why the dashboard shows a chart within seconds** while the AI is still
thinking.

## Step 4 — the AI pipeline

`tradingagents_service.run_analysis()` builds the engine config and executes the
agent graph (see [TRADINGAGENTS.md](TRADINGAGENTS.md)). As each agent finishes:

```
publish_report(run_id, "market_report", text)
  -> stores the text
  -> adds the name to reports_ready
  -> increments version
  -> advances progress and records stage timing
```

The dashboard notices the new `version`, fetches just that report, and shows it.

## Step 5 — normalise the decision

The engine's final output is free-form, so `_normalize_run_tradingagents_output()`
extracts a stable block: action (Buy/Hold/Sell/…), confidence, rationale,
executive summary, bull points, bear points, and any price levels.

## Step 6 — finish, in this exact order

1. Write the final artifacts, including `result.html`.
2. Publish `result_url` and the artifact URLs.
3. Set status `completed`.

The dashboard therefore treats `completed` **plus** `result_url`/`reports_ready`
as the only signal that the full report is downloadable.

## What the dashboard polls

| Endpoint | Frequency | Size |
| --- | --- | --- |
| `/runs/{id}/status` | every `poll_after_ms` (with `ETag`) | tiny, often `304` |
| `/runs/{id}/reports` | only when `version` changed | medium |
| `/runs/{id}/price-chart.json` | only when the chart becomes ready | medium |
| `/runs/{id}/result.html` | once, when completed | large |
| `/health` | rarely (slow interval once connected) | tiny |

## Failure paths

- Engine raises -> status `error`, message stored on the run, partial reports kept.
- User cancels -> the flag is seen between stages; status `cancelled`, work stops.
- Worker dies -> lease expires, run is retried on restart within its attempt budget.
