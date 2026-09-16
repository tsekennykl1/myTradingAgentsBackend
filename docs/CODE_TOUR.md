# Code tour — a reading order

Read the files in this order and the system will assemble itself in your head.
Each file starts with a docstring; read that before the code.

## 1. `app/main.py` (56 lines)
The whole application in one screen: load `.env`, allow the dashboard's origin,
start workers, mount routes. Notice how little it does.

## 2. `app/routes/runs.py` and `app/mcp_server.py`
The complete contract with the browser. Every handler is three to ten lines:
validate, call a service function, return. Skim all of them — you now know every
capability the backend has. `mcp_server.py` presents the same service functions as
assistant tools; compare `POST /runs` with `create_analysis`, then compare their
status/report readers.

## 3. `app/market_data.py`
The easiest real file. Download a price history, compute RSI, MACD, ATR,
Bollinger, stochastic, OBV, VWAP. Pure pandas, no AI, no state. If you want to
change what the chart shows, start here.

## 4. `app/services/analyze_service.py` (the big one)
Do **not** read top to bottom. Read these functions in order:

| Function | Why it matters |
| --- | --- |
| `_init_db` | The single `runs` table — this is the data model. |
| `create_or_reuse_run` | How a request becomes a queued row, and how duplicates are avoided. |
| `claim_next_run` / `release_run_lease` | Leasing: why two workers never collide. |
| `_run_in_background` | The actual pipeline, start to finish. |
| `publish_report` | Progressive results: why reports appear one by one. |
| `get_run_status` | The cheap polling payload. |
| `_normalize_run_tradingagents_output` | Free-form LLM output -> stable decision. |

Everything else in the file is a helper serving one of those seven.

## 5. `app/worker.py` (34 lines)
Now that leasing makes sense, this is trivial: claim, run, repeat.

## 6. `app/tradingagents_service.py`
The adapter. Read `normalize_engine_config()` to see exactly which knobs the
dashboard controls, and `_resolve_graph_class()` to see why it survives upstream
API changes.

## 7. `app/fast_path.py`
Optional accelerations: parallel analysts, parallel risk debate, stage selection,
in-memory data cache. Safe to disable via `.env` if you want the simplest possible
execution path while learning.

## 8. `app/artifacts.py` and `app/charts.py`
File production. `artifacts.py` decides *what* to save per run; `charts.py` does
the drawing. Note the `build_ / write_ / get_` naming convention.

## 9. `app/services/chart_service.py`
Indicator maths, `detect_signals()` and `derive_levels()` — the source of the
dashboard's "why this signal?" explanations.

## 10. `app/initialSetup.py`
The `.env` generator. Interesting mainly for its four catalogues
(`PROVIDER_MODELS`, `PROVIDER_DEFAULT_MODELS`, `PROVIDER_ENV_MAPPING`,
`GENERIC_MODEL_ALIASES`) which the dashboard's setup page mirrors.

---

## Small experiments to cement it

1. `uvicorn app.main:app --reload`, open `/docs`, submit a run from there and watch
   `/runs/{id}/status` change.
2. Set `RUN_WORKER_COUNT=1`, queue two runs, and watch the second wait.
3. Kill the server mid-run and restart it — observe the lease being reclaimed.
4. Add a print inside `publish_report()` to see reports commit one at a time.
5. `pytest -q tests/test_fast_path.py` — it stubs the engine, so it runs offline.
6. Connect an MCP inspector to `http://127.0.0.1:8000/mcp`, create a run, then
   confirm the same run appears under `GET /runs` and the dashboard History page.

## Conventions used throughout

- `_leading_underscore` = internal helper, not part of any contract.
- Imports inside functions in route files = deliberate, to avoid import cycles.
- Times are stored as UTC ISO strings; the dashboard formats them.
- Anything that must survive a restart lives in SQLite or the run's artifact
  folder — never in a module-level variable.
