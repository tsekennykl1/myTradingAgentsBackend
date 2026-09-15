from __future__ import annotations

from typing import Any

import pandas as pd


def build_price_chart_payload(ticker: str, analysis_date: str, df: pd.DataFrame) -> dict[str, Any]:
    stock_name = df["Stock"].iloc[0] if "Stock" in df.columns and not df.empty else ticker
    stock_code = df["Stock Code"].iloc[0] if "Stock Code" in df.columns and not df.empty else ticker

    rows: list[dict[str, Any]] = []
    for idx, row in df.iterrows():
        rows.append(
            {
                "date": pd.to_datetime(idx).strftime("%Y-%m-%d"),
                "open": None if pd.isna(row["Open"]) else round(float(row["Open"]), 4),
                "high": None if pd.isna(row["High"]) else round(float(row["High"]), 4),
                "low": None if pd.isna(row["Low"]) else round(float(row["Low"]), 4),
                "close": None if pd.isna(row["Close"]) else round(float(row["Close"]), 4),
                "volume": None if pd.isna(row["Volume"]) else float(row["Volume"]),
                "ema20": None if pd.isna(row["EMA20"]) else round(float(row["EMA20"]), 4),
                "ema50": None if pd.isna(row["EMA50"]) else round(float(row["EMA50"]), 4),
                "rsi14": None if pd.isna(row["RSI14"]) else round(float(row["RSI14"]), 4),
            }
        )

    return {
        "ticker": ticker,
        "analysis_date": analysis_date,
        "meta": {
            "stock_name": stock_name,
            "stock_code": stock_code,
            "visible_start": df.index.min().strftime("%Y-%m-%d") if not df.empty else None,
            "visible_end": df.index.max().strftime("%Y-%m-%d") if not df.empty else None,
            "rows": int(len(df)),
        },
        "rows": rows,
    }