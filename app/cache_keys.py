from __future__ import annotations

import hashlib
import json
from typing import Any


SNAPSHOT_SCHEMA_VERSION = "snapshot-v1"
REPORT_SCHEMA_VERSION = "report-v1"
PIPELINE_VERSION = "pipeline-v1"


def _stable_dumps(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_snapshot_key(*, ticker: str, analysis_date: str) -> str:
    payload = {
        "v": SNAPSHOT_SCHEMA_VERSION,
        "ticker": str(ticker).strip().upper(),
        "analysis_date": str(analysis_date).strip(),
    }
    return _sha256_text(_stable_dumps(payload))


def make_report_key(
    *,
    ticker: str,
    analysis_date: str,
    model: str,
    interval: str | None,
    strategy: str | None,
    params: dict[str, Any],
) -> str:
    payload = {
        "v": REPORT_SCHEMA_VERSION,
        "pipeline": PIPELINE_VERSION,
        "ticker": str(ticker).strip().upper(),
        "analysis_date": str(analysis_date).strip(),
        "model": str(model).strip(),
        "interval": interval,
        "strategy": strategy,
        "params": params or {},
    }
    return _sha256_text(_stable_dumps(payload))


def make_payload_fingerprint(payload: dict[str, Any]) -> str:
    return _sha256_text(_stable_dumps(payload))