"""Download US-listed stock codes and names from the Nasdaq Trader symbol directory.

Source of truth (both free, no API key, refreshed by Nasdaq every business day):

* ``nasdaqlisted.txt`` -- every security listed on Nasdaq.
* ``otherlisted.txt``  -- every security listed on NYSE, NYSE American,
  NYSE Arca, Cboe BZX, IEX and the other US exchanges.

Both are pipe-delimited text files. :func:`load_rows` downloads them (or reuses
a copy saved less than ``US_SECURITIES_MAX_AGE_HOURS`` ago in
``DATA_DIR/sources/``), drops test issues and instruments the analysis engine
cannot price well (preferreds, warrants, rights, units, notes), and returns
rows in the shape of the shared ``securities`` table.

If Nasdaq cannot be reached the last saved copy is used, so a backend that has
started once keeps its US directory while offline.
"""

from __future__ import annotations

import io
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).expanduser()
CACHE_DIR = DATA_DIR / "sources"

NASDAQ_LISTED_URL = os.getenv(
    "US_SECURITIES_NASDAQ_URL", "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
)
OTHER_LISTED_URL = os.getenv(
    "US_SECURITIES_OTHER_URL", "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"
)
MAX_AGE_SECONDS = float(os.getenv("US_SECURITIES_MAX_AGE_HOURS", "24")) * 3600
TIMEOUT_SECONDS = float(os.getenv("US_SECURITIES_TIMEOUT_SECONDS", "30"))

# otherlisted.txt "Exchange" column.
OTHER_EXCHANGES = {
    "A": "NYSE American",
    "N": "NYSE",
    "P": "NYSE Arca",
    "Z": "Cboe BZX",
    "V": "IEX",
    "M": "NYSE Texas",
}

# Names that mark instruments other than ordinary shares, ADRs and ETFs.
_EXCLUDED_NAME = re.compile(
    r"\bwarrants?\b|\brights?\b|\bunits?\s*(,|each|$)|\bpreferred (stock|shares|securities)\b"
    r"|\bpreference shares\b|\bnotes? due\b|\bdebentures?\b|\bsubordinated\b",
    re.IGNORECASE,
)
# Share-class boilerplate trimmed from the end of a name.
_NAME_SUFFIX = re.compile(
    r"\s*[-,]?\s*(class [a-z] )?(common stock|common shares|ordinary shares|"
    r"american depositary shares.*|american depository shares.*|shares of beneficial interest)\s*$",
    re.IGNORECASE,
)


def normalize_ticker(text: str) -> Optional[str]:
    """Return the price-data (Yahoo style) ticker: ``brk.b`` -> ``BRK-B``.

    Only letter codes, optionally with one share-class suffix, are US tickers.
    """
    raw = (text or "").strip().upper()
    if raw.endswith(".US"):
        raw = raw[:-3]
    if not re.fullmatch(r"[A-Z]{1,5}([.\-/][A-Z]{1,2})?", raw):
        return None
    return re.sub(r"[./]", "-", raw)


def clean_name(name: str) -> str:
    """``Apple Inc. - Common Stock`` -> ``Apple Inc.``."""
    text = re.sub(r"\s+", " ", str(name or "")).strip()
    text = _NAME_SUFFIX.sub("", text).strip()
    return text.rstrip(" -,") or str(name or "").strip()


def _download(url: str, cache_file: Path) -> str:
    """Return the file text, using a recent saved copy or downloading a new one."""
    fresh = cache_file.is_file() and (time.time() - cache_file.stat().st_mtime) < MAX_AGE_SECONDS
    if fresh:
        return cache_file.read_text(encoding="utf-8")
    try:
        response = requests.get(
            url, timeout=TIMEOUT_SECONDS, headers={"User-Agent": "TradingAnalysisAPI/1.1 (+securities directory)"}
        )
        response.raise_for_status()
        text = response.text
        if "|" not in text.splitlines()[0]:
            raise ValueError(f"Unexpected content from {url}")
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp = cache_file.with_suffix(".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(cache_file)
        return text
    except Exception as exc:
        if cache_file.is_file():
            logger.warning("US securities download failed (%s); using saved copy %s", exc, cache_file)
            return cache_file.read_text(encoding="utf-8")
        raise


def _frame(text: str) -> pd.DataFrame:
    frame = pd.read_csv(io.StringIO(text), sep="|", dtype=str, keep_default_na=False)
    # The last line is "File Creation Time: ..." -- not a security.
    first = frame.columns[0]
    return frame.loc[~frame[first].str.startswith("File Creation Time")]


def parse_nasdaq_listed(text: str) -> list[dict[str, Any]]:
    rows = []
    for _, item in _frame(text).iterrows():
        if item.get("Test Issue") == "Y":
            continue
        rows.append(_row(item["Symbol"], item["Security Name"], "NASDAQ", item.get("ETF") == "Y"))
    return [row for row in rows if row]


def parse_other_listed(text: str) -> list[dict[str, Any]]:
    rows = []
    for _, item in _frame(text).iterrows():
        if item.get("Test Issue") == "Y":
            continue
        exchange = OTHER_EXCHANGES.get(item.get("Exchange", ""), item.get("Exchange") or None)
        rows.append(_row(item["ACT Symbol"], item["Security Name"], exchange, item.get("ETF") == "Y"))
    return [row for row in rows if row]


def _row(symbol: str, name: str, exchange: Optional[str], etf: bool) -> Optional[dict[str, Any]]:
    code = (symbol or "").strip().upper()
    if not code or "$" in code:  # "$" marks preferred series, e.g. ABR$D
        return None
    if not etf and _EXCLUDED_NAME.search(name or ""):
        return None
    ticker = normalize_ticker(code)
    if not ticker:
        return None
    return {
        "ticker": ticker,
        "code": code,
        "name_en": clean_name(name),
        "name_zh": None,
        "market": "US",
        "exchange": exchange,
        "category": "ETF" if etf else "Equity",
    }


def load_rows() -> list[dict[str, Any]]:
    """Download (or reuse) both Nasdaq Trader files and return merged US rows."""
    nasdaq = parse_nasdaq_listed(_download(NASDAQ_LISTED_URL, CACHE_DIR / "nasdaqlisted.txt"))
    other = parse_other_listed(_download(OTHER_LISTED_URL, CACHE_DIR / "otherlisted.txt"))
    merged: dict[str, dict[str, Any]] = {}
    for row in nasdaq + other:
        merged.setdefault(row["ticker"], row)
    if not merged:
        raise ValueError("No US securities were found in the Nasdaq Trader directory.")
    return list(merged.values())
