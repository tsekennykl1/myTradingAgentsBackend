# Operations

Runs are persisted in SQLite and claimed by a bounded worker pool. Mount `data/` and `artifacts/` on persistent storage. Set `PUBLIC_BASE_URL` to the external HTTPS API origin and list allowed dashboards in `FRONTEND_ORIGINS`.

`GET /runs/{id}/status` is the polling contract. It returns readiness fields, a monotonic `version`, `poll_after_ms`, and an ETag. Send `If-None-Match`; an unchanged run returns 304 without a body.

Interrupted leases return to the queue at startup. Failed attempts use bounded retry controlled by `RUN_MAX_ATTEMPTS`. TradingAgents versions exposing `graph.stream()` publish reports progressively; older versions publish all reports after `propagate()` returns.
