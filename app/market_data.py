"""Market data: download prices and compute technical indicators.

download_price_frame() pulls OHLCV history (yfinance) for a ticker and date, and
the compute_* helpers add the indicators the dashboard shows: RSI, ATR, MACD,
Bollinger bands, stochastic, OBV and rolling VWAP. Everything here is plain
pandas maths - no AI, no network beyond the price download - which makes it the
easiest file in the project to read and test.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import yfinance as yf


def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["Close"].shift(1)

    tr1 = df["High"] - df["Low"]
    tr2 = (df["High"] - prev_close).abs()
    tr3 = (df["Low"] - prev_close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return atr


def compute_macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()

    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line

    return macd_line, signal_line, hist


def compute_bollinger(
    close: pd.Series,
    period: int = 20,
    std_mult: float = 2.0,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    middle = close.rolling(window=period, min_periods=period).mean()
    std = close.rolling(window=period, min_periods=period).std(ddof=0)

    upper = middle + (std * std_mult)
    lower = middle - (std * std_mult)

    return upper, middle, lower


def compute_stochastic(
    df: pd.DataFrame,
    k_period: int = 14,
    d_period: int = 3,
) -> tuple[pd.Series, pd.Series]:
    lowest_low = df["Low"].rolling(window=k_period, min_periods=k_period).min()
    highest_high = df["High"].rolling(window=k_period, min_periods=k_period).max()

    denom = (highest_high - lowest_low).replace(0, np.nan)
    k = ((df["Close"] - lowest_low) / denom) * 100
    d = k.rolling(window=d_period, min_periods=d_period).mean()

    return k, d


def compute_obv(df: pd.DataFrame) -> pd.Series:
    direction = np.sign(df["Close"].diff()).fillna(0.0)
    signed_volume = np.where(direction > 0, df["Volume"], np.where(direction < 0, -df["Volume"], 0.0))
    obv = pd.Series(signed_volume, index=df.index).cumsum()
    return obv


def compute_rolling_vwap(df: pd.DataFrame, window: int = 20) -> pd.Series:
    typical_price = (df["High"] + df["Low"] + df["Close"]) / 3.0
    tpv = typical_price * df["Volume"]

    rolling_tpv = tpv.rolling(window=window, min_periods=window).sum()
    rolling_vol = df["Volume"].rolling(window=window, min_periods=window).sum()

    vwap = rolling_tpv / rolling_vol.replace(0, np.nan)
    return vwap


def _range_to_visible_days(range_str: str) -> int:
    mapping = {
        "6mo": 183,
        "1y": 365,
        "3y": 365 * 3,
        "5y": 365 * 5,
        "max": 365 * 20,
    }
    return mapping.get(range_str, 365 * 3)


def download_price_frame(
    ticker: str,
    analysis_date: str | None,
    range_str: str = "3y",
) -> pd.DataFrame:
    end_dt = (
        pd.to_datetime(analysis_date).normalize()
        if analysis_date
        else pd.Timestamp.utcnow().normalize()
    )

    visible_days = _range_to_visible_days(range_str)
    visible_start_dt = end_dt - pd.Timedelta(days=visible_days)

    # Warmup history for long indicators
    warmup_days = 450 if range_str != "max" else 900
    yf_start_dt = visible_start_dt - pd.Timedelta(days=warmup_days)

    df = yf.download(
        ticker,
        start=yf_start_dt.strftime("%Y-%m-%d"),
        end=(end_dt + pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
        auto_adjust=True,
        progress=False,
        threads=True,
        interval="1d",
    )

    if df is None or df.empty:
        raise ValueError(f"No price data found for ticker {ticker}")

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]

    required_cols = {"Open", "High", "Low", "Close", "Volume"}
    missing = required_cols - set(df.columns)
    if missing:
        raise ValueError(f"Price data missing required columns: {sorted(missing)}")

    df = df.copy()
    df.index = pd.to_datetime(df.index)

    for col in ["Open", "High", "Low", "Close", "Volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df.dropna(subset=["Open", "High", "Low", "Close"], inplace=True)

    # Base analytics
    df["LogReturn"] = np.log(df["Close"] / df["Close"].shift(1))

    # SMA
    df["SMA20"] = df["Close"].rolling(window=20, min_periods=20).mean()
    df["SMA50"] = df["Close"].rolling(window=50, min_periods=50).mean()
    df["SMA200"] = df["Close"].rolling(window=200, min_periods=200).mean()

    # EMA
    df["EMA12"] = df["Close"].ewm(span=12, adjust=False).mean()
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA26"] = df["Close"].ewm(span=26, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()

    # Volume MA
    df["VolumeMA20"] = df["Volume"].rolling(window=20, min_periods=20).mean()

    # RSI
    df["RSI14"] = compute_rsi(df["Close"], 14)

    # MACD
    df["MACD"], df["MACDSignal"], df["MACDHist"] = compute_macd(df["Close"], 12, 26, 9)

    # Bollinger Bands
    df["BBUpper"], df["BBMiddle"], df["BBLower"] = compute_bollinger(df["Close"], 20, 2.0)

    # ATR
    df["ATR14"] = compute_atr(df, 14)

    # Stochastic
    df["StochK"], df["StochD"] = compute_stochastic(df, 14, 3)

    # OBV
    df["OBV"] = compute_obv(df)

    # Rolling VWAP 20
    df["VWAP20"] = compute_rolling_vwap(df, 20)

    # Trim to visible range only after indicators are computed
    df = df.loc[df.index >= visible_start_dt].copy()

    stock_name = ticker
    try:
        tkr = yf.Ticker(ticker)
        info = getattr(tkr, "info", {}) or {}
        stock_name = info.get("shortName") or info.get("longName") or ticker
    except Exception:
        stock_name = ticker

    df["Stock"] = stock_name
    df["Stock Code"] = ticker

    return df


def _f(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), 4)


def df_to_price_payload(df: pd.DataFrame) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    for idx, row in df.iterrows():
        out.append(
            {
                "date": pd.to_datetime(idx).strftime("%Y-%m-%d"),
                "open": _f(row.get("Open")),
                "high": _f(row.get("High")),
                "low": _f(row.get("Low")),
                "close": _f(row.get("Close")),
                "volume": None if pd.isna(row.get("Volume")) else float(row.get("Volume")),

                "volume_ma20": _f(row.get("VolumeMA20")),

                "sma20": _f(row.get("SMA20")),
                "sma50": _f(row.get("SMA50")),
                "sma200": _f(row.get("SMA200")),

                "ema12": _f(row.get("EMA12")),
                "ema20": _f(row.get("EMA20")),
                "ema26": _f(row.get("EMA26")),
                "ema50": _f(row.get("EMA50")),

                "rsi14": _f(row.get("RSI14")),

                "macd": _f(row.get("MACD")),
                "macd_signal": _f(row.get("MACDSignal")),
                "macd_hist": _f(row.get("MACDHist")),

                "bb_upper": _f(row.get("BBUpper")),
                "bb_middle": _f(row.get("BBMiddle")),
                "bb_lower": _f(row.get("BBLower")),

                "atr14": _f(row.get("ATR14")),

                "stoch_k": _f(row.get("StochK")),
                "stoch_d": _f(row.get("StochD")),

                "obv": _f(row.get("OBV")),

                # rolling_vwap_20 exposed as frontend field "vwap"
                "vwap": _f(row.get("VWAP20")),
            }
        )

    return out