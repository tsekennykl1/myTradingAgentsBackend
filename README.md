# myTradingAgents Backend

A small FastAPI service that runs multi-agent stock analyses with the
[TradingAgents](https://github.com/TauricResearch/TradingAgents) framework and serves the
results to the **My Trading Agent** React dashboard.

> **Acknowledgement.** The agent framework in `TradingAgents/` is the work of
> Tauric Research — Yijia Xiao, Edward Sun, Di Luo and Wei Wang, *"TradingAgents:
> Multi-Agents LLM Financial Trading Framework"* (2024),
> [arXiv:2412.20138](https://arxiv.org/abs/2412.20138). This repository **wraps**
> that framework; nothing inside `TradingAgents/` is modified.

---

## 1. Read this first: the 60-second mental model

```
Browser (React dashboard)
        |  HTTP/JSON
        v
FastAPI app  (app/main.py -> app/routes/*)
        |  writes a "queued" row
        v
SQLite  data/app.db          <-- one row = one analysis ("run")
        ^
        |  worker threads claim queued rows (app/worker.py)
        v
analyze_service._run_in_background()
        |-- market data (yfinance)  -> app/market_data.py
        |-- charts + files          -> app/charts.py, app/artifacts.py
        `-- AI analysis             -> app/tradingagents_service.py
                                          -> TradingAgents/ (LLM agents)
```

Three ideas explain almost everything:

1. **A run is a database row.** Submitting an analysis never blocks; it inserts a
   row with status `queued` and returns a `run_id`.
2. **Workers do the slow work.** Background threads take one queued run at a time
   and execute the pipeline, writing progress back into the same row.
3. **The dashboard polls cheaply.** `GET /runs/{id}/status` returns only small
   readiness flags and a version number (with `ETag` / `304`), so the browser
   fetches heavy reports and charts *only* when something new is ready.

---

## 2. Quick start

```bash
git clone --recurse-submodules https://github.com/tsekennykl1/myTradingAgentsBackend.git
cd myTradingAgentsBackend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python app/initialSetup.py          # guided .env creation (API keys, models)
# or:  cp .env.example .env  and edit it by hand

uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Check it: <http://127.0.0.1:8000/health> and the auto-generated API explorer at
<http://127.0.0.1:8000/docs>.

Two settings matter for the dashboard:

| Variable | Why |
| --- | --- |
| `PUBLIC_BASE_URL` | Base URL written into artifact links (e.g. `http://127.0.0.1:8000`). |
| `FRONTEND_ORIGINS` | Comma-separated browser origins allowed by CORS. Missing entries here are the usual cause of failed `OPTIONS` preflight requests. |

---

## 3. Repository map

| Path | What it is |
| --- | --- |
| `app/main.py` | FastAPI app: env loading, CORS, startup, `/` and `/health`. |
| `app/routes/runs.py` | Every run and artifact endpoint (the dashboard's contract). |
| `app/routes/providers.py` | Which LLM provider is active, key checks, credit balance. |
| `app/services/analyze_service.py` | Run lifecycle: SQLite storage, queue, progress, pipeline. |
| `app/services/chart_service.py` | Indicator maths, signal detection, `/chart/{symbol}`. |
| `app/tradingagents_service.py` | Version-tolerant adapter to the TradingAgents framework. |
| `app/fast_path.py` | Optional speed-ups: parallel analysts/risk debaters, data cache. |
| `app/market_data.py` | Price download plus RSI/MACD/ATR/Bollinger/etc. |
| `app/charts.py` | Plotly HTML and matplotlib PNG rendering. |
| `app/artifacts.py` | Per-run files: market data, chart payloads, `result.html`. |
| `app/worker.py` | The background threads that execute queued runs. |
| `app/initialSetup.py` | Interactive and unattended `.env` generator and validator. |
| `app/schemas/chart.py` | Typed response models for the chart endpoint. |
| `TradingAgents/` | Upstream framework (git submodule). **Do not edit.** |
| `tests/` | pytest suite (`pytest -q`). |

Every file above starts with a docstring explaining its role — open one and read
the top 20 lines before the code.

### Deeper guides

| Document | Read it for |
| --- | --- |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Layers, threading, storage, failure handling. |
| [docs/DATA_FLOW.md](docs/DATA_FLOW.md) | One analysis end to end, step by step. |
| [docs/API.md](docs/API.md) | Every endpoint with request and response shapes. |
| [docs/TRADINGAGENTS.md](docs/TRADINGAGENTS.md) | The agents, the debates, and how we call them. |
| [docs/CODE_TOUR.md](docs/CODE_TOUR.md) | A suggested reading order for the source. |
| [OPERATIONS.md](OPERATIONS.md) | Deployment, environment variables, tuning. |

---

## 4. What a run actually does

1. `POST /runs` validates the payload, hashes it (identical re-submits reuse the
   existing run) and inserts a `queued` row. Responds `202` with `run_id`.
2. A worker claims the row with a time-limited **lease** and sets `running`.
3. Prices are downloaded and the first artifacts are written, so the chart appears
   long before the AI is done.
4. The TradingAgents graph runs. Each agent's report is **published the moment it
   finishes**, bumping the run's `version`.
5. The Portfolio Manager's decision is normalised into a stable shape
   (action, confidence, rationale, bull/bear points).
6. Final artifacts (`result.html`) are written, then the run is marked
   `completed` — in that order, so "completed" always means "everything is there".

Cancellation is a flag in SQLite checked between stages. A crash mid-run leaves a
lease that expires; startup reclaims it and retries up to `RUN_MAX_ATTEMPTS`.

---

## 5. Data and storage

| Where | What |
| --- | --- |
| `data/app.db` (SQLite) | Runs: request, status, progress, logs, timings, reports, decision. |
| `ARTIFACTS_DIR/<run_id>/` | `market-data.json`, `price-chart.json/.png/.html`, `result.html`. |
| Process memory | Short-lived caches for repeated market/news lookups (`app/fast_path.py`). |

Both directories must live on a **persistent volume**. Because state is local,
running more than one instance behind a load balancer needs shared storage first.

---

## 6. AI providers and keys

`app/initialSetup.py` holds the catalogue the dashboard mirrors:

- `PROVIDER_MODELS` — valid model names per provider
- `PROVIDER_DEFAULT_MODELS` — sensible quick/deep defaults
- `PROVIDER_ENV_MAPPING` — which `.env` key each provider needs
- `GENERIC_MODEL_ALIASES` — friendly names mapped to real model IDs

`app/routes/providers.py` exposes this to the dashboard: which provider is active,
whether a key works, and — for the providers that publish it (DeepSeek,
OpenRouter) — the remaining credit. Other providers only allow a "key works"
check; no balance is invented. Market data keys (Alpha Vantage, FRED) live in the
same `.env`.

---

## 7. MCP (Model Context Protocol)

This backend **does not run an MCP server** today. It is a plain HTTP/JSON service,
and the dashboard is its only client. If you ever want an AI assistant to drive it
directly, the clean path is a thin MCP server that wraps the existing endpoints
(`create_run`, `get_status`, `get_reports`) rather than new logic — the service
layer in `app/services/analyze_service.py` is already the right seam for it.
Inside `TradingAgents/`, agents reach the outside world through their own tool
interfaces, not MCP.

---

## 8. Tests

```bash
pip install -r requirements-dev.txt
pytest -q
```

`tests/test_readiness.py` covers the status/readiness contract;
`tests/test_fast_path.py` covers stage selection and the parallel execution path
against a stubbed engine, so no API keys or network are needed.

---

## 9. License and credit

The wrapper code in `app/` is yours to use. The vendored framework in
`TradingAgents/` remains under its upstream license and copyright (Tauric
Research). Please keep the citation in section 1 when you share this project.
