"""Import English and Chinese HKEX security names into SQLite.

The two official HKEX workbooks in ``app/data`` are the source of truth:

* ``ListOfSecurities.xlsx`` contains English names and categories.
* ``ListOfSecurities_c.xlsx`` contains Chinese names for the same stock codes.

On the first lookup after each backend start, :func:`ensure_loaded` reads both
workbooks, joins their rows by stock code, validates the result, and replaces
the ``market = 'HK'`` rows of the shared ``securities`` table in
``DATA_DIR/app.db`` (see :mod:`app.securities`). No generated JSON file is
needed. Updating the directory therefore only requires replacing both XLSX
files with a newer matching pair from HKEX and restarting the backend.

Only instruments suitable for the analysis engine are imported. Derivative
warrants, callable bull/bear contracts, and debt securities are excluded.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Optional

import pandas as pd

SOURCE_DIR = Path(__file__).resolve().parent
ENGLISH_FILE = Path(os.getenv("HK_SECURITIES_EN_FILE", SOURCE_DIR / "ListOfSecurities.xlsx"))
CHINESE_FILE = Path(os.getenv("HK_SECURITIES_ZH_FILE", SOURCE_DIR / "ListOfSecurities_c.xlsx"))

INCLUDED_CATEGORIES = {
    "Equity",
    "Exchange Traded Products",
    "Real Estate Investment Trusts",
    "Equity Warrants (Main Board)",
    "Equity Warrants (GEM)",
}


def normalize_ticker(text: str) -> Optional[str]:
    """Return canonical ``nnnn.HK`` form for a valid numeric HK code."""
    raw = (text or "").strip().upper()
    digits = raw[:-3] if raw.endswith(".HK") else raw
    if not re.fullmatch(r"\d{1,5}", digits.strip()):
        return None
    number = int(digits)
    if number <= 0:
        return None
    return (f"{number:04d}" if number < 10000 else str(number)) + ".HK"


def is_hk_ticker(text: str) -> bool:
    return (text or "").strip().upper().endswith(".HK")


def _clean(value: Any) -> Optional[str]:
    """Convert a spreadsheet value to trimmed text without turning NaN into text."""
    if pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _code(value: Any) -> Optional[str]:
    """Return HKEX's five-digit code, preserving leading zeroes."""
    text = _clean(value)
    if not text:
        return None
    digits = text.split(".", 1)[0]
    if not digits.isdigit() or int(digits) <= 0:
        return None
    return digits.zfill(5)


def _read_sources() -> list[tuple[str, str, Optional[str], Optional[str], Optional[str]]]:
    """Read, validate, and merge the matching HKEX English/Chinese workbooks."""
    missing = [str(path) for path in (ENGLISH_FILE, CHINESE_FILE) if not path.is_file()]
    if missing:
        raise FileNotFoundError("Missing HKEX source workbook(s): " + ", ".join(missing))

    # Row 3 contains the real headings; the first two rows are title/date notes.
    english = pd.read_excel(ENGLISH_FILE, sheet_name="ListOfSecurities", header=2, dtype=str)
    chinese = pd.read_excel(CHINESE_FILE, sheet_name="ListOfSecurities", header=2, dtype=str)

    required_en = {"Stock Code", "Name of Securities", "Category"}
    required_zh = {"股份代號", "股份名稱"}
    if not required_en.issubset(english.columns) or not required_zh.issubset(chinese.columns):
        raise ValueError("The HKEX workbook columns have changed; expected stock code and name columns.")

    english = english.loc[english["Category"].isin(INCLUDED_CATEGORIES)].copy()
    english["code"] = english["Stock Code"].map(_code)
    chinese["code"] = chinese["股份代號"].map(_code)
    if english["code"].isna().any() or chinese["code"].isna().any():
        raise ValueError("An HKEX workbook contains an invalid stock code.")
    if english["code"].duplicated().any() or chinese["code"].duplicated().any():
        raise ValueError("An HKEX workbook contains duplicate stock codes.")

    names_zh = chinese.set_index("code")["股份名稱"]
    missing_zh = sorted(set(english["code"]) - set(names_zh.index))
    if missing_zh:
        preview = ", ".join(missing_zh[:5])
        raise ValueError(f"Chinese HKEX names are missing for {len(missing_zh)} codes (for example: {preview}).")

    rows = []
    for _, row in english.iterrows():
        code = str(row["code"])
        ticker = normalize_ticker(code)
        if ticker:
            rows.append(
                (ticker, code, _clean(row["Name of Securities"]), _clean(names_zh[code]), _clean(row["Category"]))
            )
    if not rows:
        raise ValueError("No supported securities were found in the HKEX workbooks.")
    return rows


def load_rows() -> list[dict[str, Any]]:
    """HK rows in the shape of the shared ``securities`` table."""
    return [
        {
            "ticker": ticker,
            "code": code,
            "name_en": name_en,
            "name_zh": name_zh,
            "market": "HK",
            "exchange": "HKEX",
            "category": category,
        }
        for ticker, code, name_en, name_zh, category in _read_sources()
    ]


def ensure_loaded() -> int:
    """Refresh the HK rows of the ``securities`` table once per process."""
    from app import securities

    return securities.ensure_loaded(["HK"])["HK"]


def lookup(ticker: str) -> Optional[dict[str, Any]]:
    """Return both HKEX names and the category for one HK ticker."""
    from app import securities

    if not normalize_ticker(ticker):
        return None
    return securities.lookup(ticker)


def display_name(ticker: str) -> Optional[str]:
    """Return a combined name such as ``TENCENT 騰訊控股``."""
    row = lookup(ticker)
    if not row:
        return None
    return " ".join(str(value) for value in (row.get("name_en"), row.get("name_zh")) if value) or None
