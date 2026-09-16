# Architecture

## Layers

```
 Client layer      React dashboard over HTTP
                   AI assistants over Streamable HTTP MCP
 ---------------------------------------------------------------
 Interface layer   app/routes/*.py, app/mcp_server.py
                   validate input, shape responses, no business logic
 ---------------------------------------------------------------
 Service layer     app/services/analyze_service.py   run lifecycle
                   app/services/chart_service.py     chart analysis
 ---------------------------------------------------------------
 Adapters          app/tradingagents_service.py  -> tradingagents pip package
                   app/market_data.py            -> yfinance prices
                   app/charts.py                 -> Plotly / matplotlib
                   app/artifacts.py              -> files on disk
 ---------------------------------------------------------------
 Storage           SQLite data/app.db   +   ARTIFACTS_DIR/<run_id>/
```

The rule to remember: **routes never compute, services never render HTTP.** If you
need new behaviour, add it in the service layer and expose it with a three-line
route or MCP tool handler. Both interfaces call the same functions, so there is
only one queue and one history.

## MCP boundary

`app/mcp_server.py` is an adapter, not another analysis engine. Its tools validate
bounded arguments and call `analyze_service`, artifact readers, or the existing
market/provider helpers. An analysis remains asynchronous: create returns a run id,
status is polled, and reports are fetched when ready. The MCP is public and has no
user identity; network controls and its tool allow-list are the security boundary.

## Concurrency model

- FastAPI handles requests on the main process; all handlers are fast and
  non-blocking because they only touch SQLite.
- `app/worker.py` starts `RUN_WORKER_COUNT` daemon threads at startup. Each loops:
  claim a queued run, execute it, release the lease.
- **Leasing** prevents double execution: `claim_next_run()` atomically marks a row
  with a worker id and an expiry. Only the lease holder may write results.
- Within one run, `app/fast_path.py` may additionally fan out the four analysts,
  and the three risk debaters, across a thread pool — then merges their outputs in
  a fixed order (Aggressive, Conservative, Neutral) so results stay deterministic.

## Failure and recovery

| Situation | Behaviour |
| --- | --- |
| Duplicate submission | Payload hash matches an active run -> that run is reused. |
| User cancels | `cancel_requested` flag in SQLite, checked between stages. |
| Process killed mid-run | Lease expires; startup reconciliation requeues it, up to `RUN_MAX_ATTEMPTS`. |
| Engine not installed | `tradingagents_service` returns a clearly-marked mock result so the UI still works. |
| Provider key missing | `validate_provider_env_or_raise()` fails the run early with a readable message. |
| Artifact file deleted | `get_*` rebuilds it on demand from the stored run. |

## Why polling is cheap

`GET /runs/{id}/status` reads only indexed columns and returns readiness booleans,
a monotonic `version`, a `poll_after_ms` hint and an `ETag`. The dashboard sends
`If-None-Match` and usually receives `304 Not Modified`. Heavy endpoints (reports,
market data, charts) are fetched only when `version` changes or a readiness flag
flips to true.

## Boundaries worth keeping

- `tradingagents` is an upstream pip package (installed from the public GitHub
  repository), never edited and no longer vendored here. All compatibility
  work lives in `app/tradingagents_service.py`.
- Chart maths (`chart_service.py`, `market_data.py`) is pure and deterministic —
  easy to unit test, no AI involved.
- Anything user-visible that must survive a restart belongs in SQLite or in the
  run's artifact folder, never in a module-level Python variable.
