"""Company fundamentals endpoints (Alpha Vantage).

The dashboard shows a small "market context" strip above the price chart:
who the company is, what it is worth today, and how its recent earnings
went. None of that comes from the TradingAgents framework -- it is a plain
read of the Alpha Vantage REST API using ALPHA_VANTAGE_API_KEY from .env,
so nothing under /TradingAgents is touched.

Endpoints
---------
GET /market/company?ticker=AAPL
    Profile (name, sector, industry, currency), valuation (market cap,
    P/E, dividend yield, 52-week range) and the latest quote.

GET /market/earnings?ticker=AAPL&quarters=4
    Recent quarterly EPS: reported vs estimated, surprise %, plus the
    latest annual EPS series for a trend line.

Both answer 200 with ``available: false`` and a human-readable ``message``
when the key is missing, the free-tier rate limit is hit, or the symbol is
not covered (Alpha Vantage has thin coverage outside US listings, e.g.
Hong Kong tickers such as 0700.HK). The dashboard then simply hides the
strip instead of showing an error.

Responses are cached in memory for six hours per symbol because the free
tier allows only 25 requests a day.
"""

from __future__ import annotations

import os
import time
from typing import Any

import requests
from fastapi import APIRouter, Query

router = APIRouter(prefix="/market", tags=["market"])

ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
CACHE_TTL_SECONDS = 6 * 60 * 60
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _api_key() -> str:
    return (os.getenv("ALPHA_VANTAGE_API_KEY") or "").strip()


def _cached(key: str) -> dict[str, Any] | None:
    hit = _cache.get(key)
    if not hit:
        return None
    stored_at, value = hit
    if time.time() - stored_at > CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return value


def _store(key: str, value: dict[str, Any]) -> dict[str, Any]:
    # Only cache useful answers; a rate-limit message should be retried later.
    if value.get("available"):
        _cache[key] = (time.time(), value)
    return value


def _unavailable(message: str) -> dict[str, Any]:
    return {"available": False, "message": message}


def _fetch(params: dict[str, str]) -> tuple[dict[str, Any] | None, str | None]:
    """Call Alpha Vantage. Returns (payload, error message)."""
    key = _api_key()
    if not key:
        return None, "No ALPHA_VANTAGE_API_KEY in .env -- add one on the Connect page."
    try:
        response = requests.get(
            ALPHA_VANTAGE_URL, params={**params, "apikey": key}, timeout=15
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # network error, timeout, bad JSON
        return None, f"Alpha Vantage request failed: {exc}"

    if not isinstance(payload, dict) or not payload:
        return None, "Alpha Vantage returned an empty response."
    # The free tier signals problems in prose fields rather than status codes.
    for field in ("Note", "Information", "Error Message"):
        if payload.get(field):
            return None, str(payload[field])
    return payload, None


def _number(value: Any) -> float | None:
    if value in (None, "", "None", "-"):
        return None
    try:
        return float(str(value).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


@router.get("/company")
def company(ticker: str = Query(..., min_length=1)) -> dict[str, Any]:
    """Company profile, valuation and latest quote for one symbol."""
    symbol = ticker.strip().upper()
    cache_key = f"company:{symbol}"
    cached = _cached(cache_key)
    if cached:
        return cached

    overview, overview_error = _fetch({"function": "OVERVIEW", "symbol": symbol})
    quote_payload, quote_error = _fetch({"function": "GLOBAL_QUOTE", "symbol": symbol})
    quote_raw = (quote_payload or {}).get("Global Quote") or {}

    if not overview and not quote_raw:
        return _unavailable(
            overview_error
            or quote_error
            or f"Alpha Vantage has no data for {symbol}."
        )

    overview = overview or {}
    result = {
        "available": True,
        "symbol": overview.get("Symbol") or symbol,
        "name": overview.get("Name") or None,
        "sector": overview.get("Sector") or None,
        "industry": overview.get("Industry") or None,
        "exchange": overview.get("Exchange") or None,
        "currency": overview.get("Currency") or None,
        "description": (overview.get("Description") or None),
        "market_cap": _number(overview.get("MarketCapitalization")),
        "pe_ratio": _number(overview.get("PERatio")),
        "forward_pe": _number(overview.get("ForwardPE")),
        "eps": _number(overview.get("EPS")),
        "dividend_yield": _number(overview.get("DividendYield")),
        "beta": _number(overview.get("Beta")),
        "week52_high": _number(overview.get("52WeekHigh")),
        "week52_low": _number(overview.get("52WeekLow")),
        "analyst_target_price": _number(overview.get("AnalystTargetPrice")),
        "quote": {
            "price": _number(quote_raw.get("05. price")),
            "change": _number(quote_raw.get("09. change")),
            "change_percent": _number(quote_raw.get("10. change percent")),
            "previous_close": _number(quote_raw.get("08. previous close")),
            "volume": _number(quote_raw.get("06. volume")),
            "latest_trading_day": quote_raw.get("07. latest trading day") or None,
        }
        if quote_raw
        else None,
    }
    return _store(cache_key, result)


@router.get("/earnings")
def earnings(
    ticker: str = Query(..., min_length=1),
    quarters: int = Query(4, ge=1, le=12),
) -> dict[str, Any]:
    """Recent quarterly EPS (reported vs estimate) and the annual EPS series."""
    symbol = ticker.strip().upper()
    cache_key = f"earnings:{symbol}:{quarters}"
    cached = _cached(cache_key)
    if cached:
        return cached

    payload, error = _fetch({"function": "EARNINGS", "symbol": symbol})
    if not payload:
        return _unavailable(error or f"No earnings data for {symbol}.")

    quarterly_raw = payload.get("quarterlyEarnings") or []
    annual_raw = payload.get("annualEarnings") or []
    if not quarterly_raw and not annual_raw:
        return _unavailable(f"Alpha Vantage has no earnings history for {symbol}.")

    result = {
        "available": True,
        "symbol": payload.get("symbol") or symbol,
        "quarters": [
            {
                "fiscal_date": item.get("fiscalDateEnding"),
                "reported_date": item.get("reportedDate"),
                "reported_eps": _number(item.get("reportedEPS")),
                "estimated_eps": _number(item.get("estimatedEPS")),
                "surprise": _number(item.get("surprise")),
                "surprise_percent": _number(item.get("surprisePercentage")),
            }
            for item in quarterly_raw[:quarters]
        ],
        "annual": [
            {
                "fiscal_date": item.get("fiscalDateEnding"),
                "reported_eps": _number(item.get("reportedEPS")),
            }
            for item in annual_raw[:5]
        ],
    }
    return _store(cache_key, result)


@router.get("/security")
def security(ticker: str = Query(..., min_length=1)) -> dict[str, Any]:
    """HKEX directory lookup: English and Chinese name for a Hong Kong listing.

    The ``hk_securities`` table is refreshed directly from the paired English
    and Chinese HKEX XLSX source files once per backend process, so the
    dashboard can title the market snapshot before any price data arrives.

    GET /market/security?ticker=0700.HK
    -> {"available": true, "ticker": "0700.HK", "code": "00700",
        "name_en": "TENCENT", "name_zh": "騰訊控股", "category": "Equity"}
    """
    from app import hk_securities

    symbol = ticker.strip().upper()
    if not hk_securities.is_hk_ticker(symbol):
        return _unavailable("Only Hong Kong listings (.HK) are in this directory.")
    row = hk_securities.lookup(symbol)
    if not row:
        return _unavailable(f"{symbol} is not in the HKEX securities list.")
    return {"available": True, **row}
