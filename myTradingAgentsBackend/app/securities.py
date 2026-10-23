"""One stock directory for every market: code, name and market in SQLite.

The ``securities`` table in ``DATA_DIR/app.db`` maps each stock code to its
name and market::

    ticker   TEXT PRIMARY KEY  -- price-data symbol: 0700.HK, AAPL, BRK-B
    code     TEXT NOT NULL     -- exchange code: 00700, AAPL, BRK.B
    name_en  TEXT              -- English name
    name_zh  TEXT              -- Chinese name (HK only)
    market   TEXT NOT NULL     -- "HK" or "US"
    exchange TEXT              -- HKEX, NASDAQ, NYSE, NYSE Arca, ...
    category TEXT              -- Equity, ETF, REIT, ...

Each market has its own source loader:

* ``HK`` -- :mod:`app.hk_securities`, the two official HKEX XLSX workbooks.
* ``US`` -- :mod:`app.us_securities`, the Nasdaq Trader symbol directory,
  downloaded from nasdaqtrader.com (saved copy reused for 24 hours).

Every market is refreshed once per backend process. :func:`preload` runs at
startup in a background thread, so the dashboard's stock field can list
suggestions from the first keypress. A market whose source fails keeps its
previous rows, so one unreachable website never empties the directory.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).expanduser()
DB_PATH = DATA_DIR / "app.db"

MARKETS = ("HK", "US")
COLUMNS = ("ticker", "code", "name_en", "name_zh", "market", "exchange", "category")

_LOCK = threading.Lock()
_loaded: set[str] = set()


def _loaders() -> dict[str, Callable[[], list[dict[str, Any]]]]:
    from app import hk_securities, us_securities

    return {"HK": hk_securities.load_rows, "US": us_securities.load_rows}


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def _init(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS securities (
            ticker   TEXT PRIMARY KEY,
            code     TEXT NOT NULL,
            name_en  TEXT,
            name_zh  TEXT,
            market   TEXT NOT NULL,
            exchange TEXT,
            category TEXT
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_securities_market_code ON securities(market, code)")
    # The HK-only table this one replaced.
    conn.execute("DROP TABLE IF EXISTS hk_securities")


def normalize_market(market: Optional[str]) -> Optional[str]:
    value = (market or "").strip().upper()
    return value if value in MARKETS else None


def market_of(ticker: str) -> Optional[str]:
    """Infer the market from the typed code: numbers/.HK = HK, letters = US."""
    from app import hk_securities, us_securities

    if hk_securities.normalize_ticker(ticker):
        return "HK"
    if us_securities.normalize_ticker(ticker):
        return "US"
    return None


def normalize_ticker(ticker: str) -> Optional[str]:
    from app import hk_securities, us_securities

    return hk_securities.normalize_ticker(ticker) or us_securities.normalize_ticker(ticker)


def ensure_loaded(markets: Iterable[str] = MARKETS) -> dict[str, int]:
    """Refresh the requested markets once per process; return row counts."""
    wanted = [m for m in (normalize_market(m) for m in markets) if m]
    loaders = _loaders()
    with _LOCK:
        conn = _connect()
        try:
            _init(conn)
            for market in wanted:
                if market in _loaded:
                    continue
                try:
                    rows = loaders[market]()
                except Exception:
                    logger.exception("Could not load %s securities; keeping the previous rows", market)
                    continue
                with conn:
                    conn.execute("DELETE FROM securities WHERE market = ?", (market,))
                    conn.executemany(
                        f"INSERT OR REPLACE INTO securities ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                        [tuple(row.get(column) for column in COLUMNS) for row in rows],
                    )
                _loaded.add(market)
                logger.info("Loaded %d %s securities", len(rows), market)
            counts = dict(conn.execute("SELECT market, COUNT(*) FROM securities GROUP BY market").fetchall())
            return {market: int(counts.get(market, 0)) for market in wanted}
        finally:
            conn.close()


def preload() -> threading.Thread:
    """Load every market in a background thread so startup is not delayed."""
    thread = threading.Thread(target=lambda: _safe_load(), name="securities-preload", daemon=True)
    thread.start()
    return thread


def _safe_load() -> None:
    try:
        ensure_loaded()
    except Exception:
        logger.exception("Securities preload failed")


def lookup(ticker: str) -> Optional[dict[str, Any]]:
    """Return the directory row for one ticker in any market, or None."""
    market = market_of(ticker)
    symbol = normalize_ticker(ticker)
    if not market or not symbol:
        return None
    ensure_loaded([market])
    conn = _connect()
    try:
        row = conn.execute(
            f"SELECT {', '.join(COLUMNS)} FROM securities WHERE ticker = ? OR (market = ? AND code = ?)",
            (symbol, market, (ticker or "").strip().upper()),
        ).fetchone()
    finally:
        conn.close()
    return dict(row) if row else None


def display_name(ticker: str) -> Optional[str]:
    """``TENCENT 騰訊控股`` for HK, ``Apple Inc.`` for US."""
    row = lookup(ticker)
    if not row:
        return None
    names = [row.get("name_en"), row.get("name_zh")]
    if names[0] and names[0] == names[1]:
        names.pop()
    return " ".join(str(name) for name in names if name) or None


def listing(market: str) -> list[dict[str, Any]]:
    """Every row for one market, ordered by code (for the dashboard preload)."""
    code = normalize_market(market)
    if not code:
        return []
    ensure_loaded([code])
    conn = _connect()
    try:
        rows = conn.execute(
            f"SELECT {', '.join(COLUMNS)} FROM securities WHERE market = ? ORDER BY code", (code,)
        ).fetchall()
    finally:
        conn.close()
    return [dict(row) for row in rows]


def search(query: str, market: Optional[str] = None, limit: int = 10) -> list[dict[str, Any]]:
    """Code-prefix matches first (shortest code first), then name matches."""
    text = (query or "").strip().upper()
    if not text:
        return []
    markets = [normalize_market(market)] if normalize_market(market) else list(MARKETS)
    ensure_loaded(markets)
    placeholders = ", ".join("?" * len(markets))
    hk_digits = text[:-3] if text.endswith(".HK") else text
    code_prefixes = {text, text.replace(".", "-")}
    if hk_digits.isdigit():
        code_prefixes.add(hk_digits.lstrip("0") or "0")
        code_prefixes.add(hk_digits.zfill(5))
    conn = _connect()
    try:
        code_clause = " OR ".join("code LIKE ? OR ticker LIKE ?" for _ in code_prefixes)
        code_args = [arg for prefix in code_prefixes for arg in (f"{prefix}%", f"{prefix}%")]
        rows = conn.execute(
            f"""
            SELECT * FROM (
                SELECT {', '.join(COLUMNS)}, 0 AS rank FROM securities
                WHERE market IN ({placeholders}) AND ({code_clause})
                UNION ALL
                SELECT {', '.join(COLUMNS)}, 1 AS rank FROM securities
                WHERE market IN ({placeholders}) AND (UPPER(name_en) LIKE ? OR name_zh LIKE ?)
            )
            ORDER BY rank, LENGTH(code), code
            LIMIT ?
            """,
            (*markets, *code_args, *markets, f"%{text}%", f"%{query.strip()}%", limit * 3),
        ).fetchall()
    finally:
        conn.close()
    seen: set[str] = set()
    result = []
    for row in rows:
        item = {column: row[column] for column in COLUMNS}
        if item["ticker"] in seen:
            continue
        seen.add(item["ticker"])
        result.append(item)
        if len(result) >= limit:
            break
    return result
