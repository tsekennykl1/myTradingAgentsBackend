# myTradingAgents Backend

A small FastAPI service that runs multi-agent stock analyses with the
[TradingAgents](https://github.com/TauricResearch/TradingAgents) framework and serves the
results to the **My Trading Agent** React dashboard.

> **Acknowledgement.** The agent framework is the work of
> Tauric Research — Yijia Xiao, Edward Sun, Di Luo and Wei Wang, *"TradingAgents:
> Multi-Agents LLM Financial Trading Framework"* (2024),
> [arXiv:2412.20138](https://arxiv.org/abs/2412.20138). This repository **wraps**
> that framework and never modifies it. It is installed as a normal pip package
> straight from the public repository (see `requirements.txt`), so no copy of the
> framework lives in this repository.

---

## 1. Read this first: the 60-second mental model

```
Browser (React dashboard) -- HTTP/JSON --+
AI assistant ------------ MCP /mcp -----+--> FastAPI app
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
                                          -> tradingagents package (LLM agents)
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
git clone https://github.com/tsekennykl1/myTradingAgentsBackend.git
cd myTradingAgentsBackend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt    # also pip-installs TradingAgents v0.4.0 from GitHub

python app/initialSetup.py          # guided .env creation (API keys, models, MCP access key)
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
| `app/mcp_server.py` | Public MCP tools that call the same run services as HTTP routes. |
| `app/routes/providers.py` | Which LLM provider is active, key checks, credit balance. |
| `app/services/analyze_service.py` | Run lifecycle: SQLite storage, queue, progress, pipeline. |
| `app/services/chart_service.py` | Indicator maths, signal detection, `/chart/{symbol}`. |
| `app/tradingagents_service.py` | Version-tolerant adapter to the TradingAgents framework. |
| `app/fast_path.py` | Optional speed-ups: parallel analysts/risk debaters, data cache. |
| `app/market_data.py` | Price download plus RSI/MACD/ATR/Bollinger/etc. |
| `app/hk_securities.py` | Imports English and Chinese stock names from the two official HKEX XLSX files into SQLite. |
| `app/data/ListOfSecurities*.xlsx` | Raw HKEX English/Chinese security lists; these are the stock-name source of truth. |
| `app/charts.py` | Plotly HTML and matplotlib PNG rendering. |
| `app/artifacts.py` | Per-run files: market data, chart payloads, `result.html`. |
| `app/worker.py` | The background threads that execute queued runs. |
| `app/initialSetup.py` | Interactive and unattended `.env` generator and validator. Asks whether to enable the MCP server and then collects or generates `MCP_ACCESS_KEY` (min 24 chars, 64 recommended, no spaces). Non-interactive configs accept `mcp_enabled`, `mcp_access_key`, `mcp_allowed_tools`, `mcp_create_runs_per_hour`. |
| `app/schemas/chart.py` | Typed response models for the chart endpoint. |
| _(no local folder)_ | The upstream framework is the pip package `tradingagents`, installed from <https://github.com/TauricResearch/TradingAgents>. **Never edited.** |
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

### Updating Hong Kong stock names

Download the latest matching English and Chinese **List of Securities** files
from HKEX. Keep their original names and replace:

```text
app/data/ListOfSecurities.xlsx
app/data/ListOfSecurities_c.xlsx
```

Restart the backend. On its first Hong Kong security lookup, it reads both
workbooks, joins them by stock code, validates them, and replaces the
`hk_securities` rows in `data/app.db`. The English workbook supplies the code,
English name, and category; the Chinese workbook supplies the Chinese name.
`app/data/hk_securities.json` is no longer used or required.

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

The app mounts a stateless Streamable HTTP MCP server at `/mcp`. ChatGPT, Claude,
Cursor, Codex, or another MCP client can use the same queue, reports, artifacts,
and provider catalogue as the React dashboard. Add this URL to the client:

```text
http://127.0.0.1:8000/mcp
```

For a hosted engine, replace the host with `PUBLIC_BASE_URL`. Tools are:

- Run control: `create_analysis`, `cancel_analysis`
- Run reads: `list_analyses`, `get_analysis`, `get_analysis_status`
- Results: `get_decision`, `list_reports`, `get_report`, `get_chart_data`
- Context: `get_market_snapshot`, `get_earnings`, `get_engine_info`, `list_ai_models`

`create_analysis` returns quickly; poll `get_analysis_status` using its
`poll_after_ms`, then read reports and charts as readiness fields change. Runs
created over MCP appear in the dashboard's Historical Analysis page because MCP
calls `analyze_service.py` directly instead of maintaining separate state.

> **Public-access warning.** There is no login on this MCP. Anyone who can reach
> `/mcp` can read all stored analyses, start runs that spend configured LLM credit,
> and cancel active runs. Every request must carry the shared `MCP_ACCESS_KEY`
> (`Authorization: Bearer <key>` or `X-MCP-Key: <key>`); requests without it get
> HTTP 401. If `MCP_ACCESS_KEY` is unset, `/mcp` refuses everything. Still
> restrict the backend at the firewall/reverse proxy when it should not be
> internet-accessible.

Operator controls in `.env`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `MCP_ACCESS_KEY` | empty (closed) | Shared key every MCP caller must send; without it `/mcp` returns 401. Generate with `openssl rand -hex 32`. |
| `MCP_ENABLED` | `1` | Set `0` before startup to omit the endpoint. |
| `MCP_ALLOWED_TOOLS` | empty (all) | Comma-separated allow-list; omit create/cancel for read-only access. |
| `MCP_CREATE_RUNS_PER_HOUR` | `12` | Process-wide cap for public analysis creation. |

The MCP never exposes API-key values, provider switching, arbitrary files, or
internal paths. Inside the `tradingagents` package, agents still use the upstream framework's
own tool interfaces; MCP is an external client interface around this backend.

---

## 8. Tests

```bash
pytest -q   # pytest is already in requirements.txt
```

`tests/test_readiness.py` covers the status/readiness contract;
`tests/test_fast_path.py` covers stage selection and the parallel execution path
against a stubbed engine, so no API keys or network are needed.

---

## 9. License and credit

The wrapper code in `app/` is yours to use. The vendored framework in
The `tradingagents` framework remains under its upstream license and copyright (Tauric
Research). Please keep the citation in section 1 when you share this project.
