"""Chart analysis service: indicators, signals and levels for the dashboard.

Powers GET /chart/{symbol}. Three layers, top to bottom in the file:
  - plain indicator maths (sma, ema, rsi, macd, bollinger, atr, stochastic, obv)
  - detect_signals() and derive_levels(), which turn those numbers into the
    human-readable "why this signal" reasons and support/resistance lines
  - build_chart_response(), which assembles the typed payload in
    app/schemas/chart.py and, when a run_id is given, merges the AI decision in.

This is deterministic maths, not AI: the same bars always give the same output.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from statistics import mean, pstdev
from typing import Optional

import pandas as pd

from app.market_data import download_price_frame
from app.schemas.chart import (
    AnalysisBlock,
    ArtifactsBlock,
    BollingerPoint,
    ChartResponse,
    IndicatorsBlock,
    LevelsBlock,
    MacdPoint,
    MetaBlock,
    PivotBlock,
    PivotClassic,
    PlotBollingerSeries,
    PlotMacdSeries,
    PlotPricePoint,
    PlotSeriesBlock,
    PlotStochasticSeries,
    PlotValuePoint,
    PriceBar,
    PriceLevel,
    RequestBlock,
    SignalPoint,
    StochasticPoint,
    TimeValue,
    TradeLevels,
)
from app.services.analyze_service import get_run


@dataclass
class RawBar:
    t: str
    o: float
    h: float
    l: float
    c: float
    v: float


def iso_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_range_to_days(range_str: str) -> int:
    mapping = {
        "6mo": 183,
        "1y": 365,
        "3y": 365 * 3,
        "5y": 365 * 5,
        "max": 365 * 20,
    }
    return mapping.get(range_str, 365 * 3)


def _resolve_analysis_date_for_chart(run_id: Optional[str]) -> Optional[str]:
    if not run_id:
        return None

    try:
        run = get_run(run_id)
    except Exception:
        return None

    if not run:
        return None

    partial = run.get("partial_result") or {}
    result = run.get("result") or {}
    payload = run.get("payload") or {}

    return (
        partial.get("analysis_date")
        or result.get("analysis_date")
        or payload.get("end_date")
        or payload.get("start_date")
    )


def _trim_df_to_range(df: pd.DataFrame, range_str: str) -> pd.DataFrame:
    if df.empty:
        return df

    if range_str == "max":
        return df

    days = parse_range_to_days(range_str)
    end_dt = pd.to_datetime(df.index.max()).normalize()
    start_dt = end_dt - pd.Timedelta(days=days)

    trimmed = df.loc[df.index >= start_dt].copy()
    return trimmed if not trimmed.empty else df


def fetch_historical_bars(
    symbol: str,
    range_str: str = "3y",
    interval: str = "1d",
    analysis_date: Optional[str] = None,
) -> list[RawBar]:
    """
    Fetch historical OHLCV bars using the app's market data pipeline.

    Notes:
    - download_price_frame() currently supports daily bars only.
    - interval is accepted for API compatibility, but only "1d" is supported.
    """
    if interval != "1d":
        raise ValueError("Only interval='1d' is currently supported by the market data provider.")

    df = download_price_frame(symbol, analysis_date, range_str=range_str)
    df = _trim_df_to_range(df, range_str)

    if df.empty:
        return []

    bars: list[RawBar] = []
    for idx, row in df.iterrows():
        open_ = row.get("Open")
        high = row.get("High")
        low = row.get("Low")
        close = row.get("Close")
        volume = row.get("Volume")

        if pd.isna(open_) or pd.isna(high) or pd.isna(low) or pd.isna(close):
            continue

        bars.append(
            RawBar(
                t=pd.to_datetime(idx).strftime("%Y-%m-%d"),
                o=round(float(open_), 4),
                h=round(float(high), 4),
                l=round(float(low), 4),
                c=round(float(close), 4),
                v=0.0 if pd.isna(volume) else float(volume),
            )
        )

    return bars


def to_price_bars(bars: list[RawBar]) -> list[PriceBar]:
    return [PriceBar(t=b.t, o=b.o, h=b.h, l=b.l, c=b.c, v=b.v) for b in bars]


def closes(bars: list[RawBar]) -> list[float]:
    return [b.c for b in bars]


def volumes(bars: list[RawBar]) -> list[float]:
    return [b.v for b in bars]


def build_time_values(bars: list[RawBar], values: list[Optional[float]]) -> list[TimeValue]:
    return [
        TimeValue(t=bar.t, value=round(v, 4) if v is not None else None)
        for bar, v in zip(bars, values, strict=False)
    ]


def build_plot_value_points(
    bars: list[RawBar],
    values: list[Optional[float]],
) -> list[PlotValuePoint]:
    return [
        PlotValuePoint(
            time=bar.t,
            value=round(v, 4) if v is not None else None,
        )
        for bar, v in zip(bars, values, strict=False)
    ]


def build_plot_price_points(bars: list[RawBar]) -> list[PlotPricePoint]:
    return [
        PlotPricePoint(
            time=bar.t,
            open=b.o,
            high=b.h,
            low=b.l,
            close=b.c,
        )
        for b in bars
    ]


def build_plot_volume_points(bars: list[RawBar]) -> list[PlotValuePoint]:
    return [
        PlotValuePoint(
            time=b.t,
            value=float(b.v),
        )
        for b in bars
    ]


def build_plot_macd_series(
    bars: list[RawBar],
    macd_points: list[dict[str, Optional[float]]],
) -> PlotMacdSeries:
    return PlotMacdSeries(
        macd=[
            PlotValuePoint(
                time=bar.t,
                value=round(point["macd"], 4) if point["macd"] is not None else None,
            )
            for bar, point in zip(bars, macd_points, strict=False)
        ],
        signal=[
            PlotValuePoint(
                time=bar.t,
                value=round(point["signal"], 4) if point["signal"] is not None else None,
            )
            for bar, point in zip(bars, macd_points, strict=False)
        ],
        histogram=[
            PlotValuePoint(
                time=bar.t,
                value=round(point["histogram"], 4) if point["histogram"] is not None else None,
            )
            for bar, point in zip(bars, macd_points, strict=False)
        ],
    )


def build_plot_bollinger_series(
    bars: list[RawBar],
    points: list[dict[str, Optional[float]]],
) -> PlotBollingerSeries:
    return PlotBollingerSeries(
        upper=[
            PlotValuePoint(
                time=bar.t,
                value=round(point["upper"], 4) if point["upper"] is not None else None,
            )
            for bar, point in zip(bars, points, strict=False)
        ],
        middle=[
            PlotValuePoint(
                time=bar.t,
                value=round(point["middle"], 4) if point["middle"] is not None else None,
            )
            for bar, point in zip(bars, points, strict=False)
        ],
        lower=[
            PlotValuePoint(
                time=bar.t,
                value=round(point["lower"], 4) if point["lower"] is not None else None,
            )
            for bar, point in zip(bars, points, strict=False)
        ],
    )


def build_plot_stochastic_series(
    bars: list[RawBar],
    points: list[dict[str, Optional[float]]],
) -> PlotStochasticSeries:
    return PlotStochasticSeries(
        k=[
            PlotValuePoint(
                time=bar.t,
                value=round(point["k"], 4) if point["k"] is not None else None,
            )
            for bar, point in zip(bars, points, strict=False)
        ],
        d=[
            PlotValuePoint(
                time=bar.t,
                value=round(point["d"], 4) if point["d"] is not None else None,
            )
            for bar, point in zip(bars, points, strict=False)
        ],
    )


def sma(values: list[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
        else:
            window = values[i + 1 - period : i + 1]
            out.append(sum(window) / period)
    return out


def ema(values: list[float], period: int) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if len(values) < period:
        return out

    multiplier = 2 / (period + 1)
    initial_sma = sum(values[:period]) / period
    out[period - 1] = initial_sma

    prev = initial_sma
    for i in range(period, len(values)):
        current = ((values[i] - prev) * multiplier) + prev
        out[i] = current
        prev = current

    return out


def rsi(values: list[float], period: int = 14) -> list[Optional[float]]:
    out: list[Optional[float]] = [None] * len(values)
    if len(values) <= period:
        return out

    gains: list[float] = []
    losses: list[float] = []

    for i in range(1, period + 1):
        diff = values[i] - values[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    rs = avg_gain / avg_loss if avg_loss != 0 else float("inf")
    out[period] = 100 - (100 / (1 + rs))

    for i in range(period + 1, len(values)):
        diff = values[i] - values[i - 1]
        gain = max(diff, 0.0)
        loss = max(-diff, 0.0)

        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period

        rs = avg_gain / avg_loss if avg_loss != 0 else float("inf")
        out[i] = 100 - (100 / (1 + rs))

    return out


def macd(
    values: list[float],
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> list[dict[str, Optional[float]]]:
    ema_fast = ema(values, fast)
    ema_slow = ema(values, slow)

    macd_line: list[Optional[float]] = []
    for fast_v, slow_v in zip(ema_fast, ema_slow, strict=False):
        if fast_v is None or slow_v is None:
            macd_line.append(None)
        else:
            macd_line.append(fast_v - slow_v)

    macd_non_null = [v if v is not None else 0.0 for v in macd_line]
    signal_line = ema(macd_non_null, signal_period)

    out: list[dict[str, Optional[float]]] = []
    for m, s in zip(macd_line, signal_line, strict=False):
        hist = None if m is None or s is None else m - s
        out.append(
            {
                "macd": m,
                "signal": s,
                "histogram": hist,
            }
        )
    return out


def bollinger(
    values: list[float],
    period: int = 20,
    stddev_mult: float = 2.0,
) -> list[dict[str, Optional[float]]]:
    out: list[dict[str, Optional[float]]] = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append({"middle": None, "upper": None, "lower": None})
        else:
            window = values[i + 1 - period : i + 1]
            mid = mean(window)
            sd = pstdev(window)
            out.append(
                {
                    "middle": mid,
                    "upper": mid + (sd * stddev_mult),
                    "lower": mid - (sd * stddev_mult),
                }
            )
    return out


def true_range(current_high: float, current_low: float, prev_close: Optional[float]) -> float:
    if prev_close is None:
        return current_high - current_low
    return max(
        current_high - current_low,
        abs(current_high - prev_close),
        abs(current_low - prev_close),
    )


def atr(bars: list[RawBar], period: int = 14) -> list[Optional[float]]:
    trs: list[float] = []
    prev_close: Optional[float] = None

    for bar in bars:
        tr = true_range(bar.h, bar.l, prev_close)
        trs.append(tr)
        prev_close = bar.c

    out: list[Optional[float]] = [None] * len(trs)
    if len(trs) < period:
        return out

    initial_atr = sum(trs[:period]) / period
    out[period - 1] = initial_atr
    prev_atr = initial_atr

    for i in range(period, len(trs)):
        current_atr = ((prev_atr * (period - 1)) + trs[i]) / period
        out[i] = current_atr
        prev_atr = current_atr

    return out


def stochastic(
    bars: list[RawBar],
    period: int = 14,
    smooth_d: int = 3,
) -> list[dict[str, Optional[float]]]:
    k_values: list[Optional[float]] = []

    for i in range(len(bars)):
        if i + 1 < period:
            k_values.append(None)
            continue

        window = bars[i + 1 - period : i + 1]
        highest_high = max(b.h for b in window)
        lowest_low = min(b.l for b in window)

        if highest_high == lowest_low:
            k_values.append(0.0)
        else:
            k = ((bars[i].c - lowest_low) / (highest_high - lowest_low)) * 100
            k_values.append(k)

    d_values: list[Optional[float]] = []
    for i in range(len(k_values)):
        recent = [v for v in k_values[max(0, i + 1 - smooth_d) : i + 1] if v is not None]
        if len(recent) < smooth_d:
            d_values.append(None)
        else:
            d_values.append(sum(recent) / smooth_d)

    out: list[dict[str, Optional[float]]] = []
    for k, d in zip(k_values, d_values, strict=False):
        out.append({"k": k, "d": d})
    return out


def obv(bars: list[RawBar]) -> list[Optional[float]]:
    if not bars:
        return []

    out: list[Optional[float]] = [0.0]
    for i in range(1, len(bars)):
        prev = out[-1] or 0.0
        if bars[i].c > bars[i - 1].c:
            out.append(prev + bars[i].v)
        elif bars[i].c < bars[i - 1].c:
            out.append(prev - bars[i].v)
        else:
            out.append(prev)
    return out


def detect_signals(
    bars: list[RawBar],
    rsi14: list[Optional[float]],
    macd_points: list[dict[str, Optional[float]]],
    sma50: list[Optional[float]],
    sma200: list[Optional[float]],
    volume_sma20: list[Optional[float]],
) -> list[SignalPoint]:
    signals: list[SignalPoint] = []

    for i in range(1, len(bars)):
        bar = bars[i]

        prev_macd = macd_points[i - 1]
        curr_macd = macd_points[i]
        if (
            prev_macd["macd"] is not None
            and prev_macd["signal"] is not None
            and curr_macd["macd"] is not None
            and curr_macd["signal"] is not None
        ):
            if prev_macd["macd"] <= prev_macd["signal"] and curr_macd["macd"] > curr_macd["signal"]:
                signals.append(
                    SignalPoint(
                        t=bar.t,
                        type="macd_bullish_crossover",
                        price=bar.c,
                        direction="bullish",
                        strength=0.7,
                        label="MACD bullish crossover",
                    )
                )
            elif prev_macd["macd"] >= prev_macd["signal"] and curr_macd["macd"] < curr_macd["signal"]:
                signals.append(
                    SignalPoint(
                        t=bar.t,
                        type="macd_bearish_crossover",
                        price=bar.c,
                        direction="bearish",
                        strength=0.7,
                        label="MACD bearish crossover",
                    )
                )

        if rsi14[i] is not None:
            if rsi14[i] >= 70:
                signals.append(
                    SignalPoint(
                        t=bar.t,
                        type="rsi_overbought",
                        price=bar.c,
                        direction="warning",
                        strength=0.55,
                        label="RSI overbought",
                    )
                )
            elif rsi14[i] <= 30:
                signals.append(
                    SignalPoint(
                        t=bar.t,
                        type="rsi_oversold",
                        price=bar.c,
                        direction="bullish",
                        strength=0.6,
                        label="RSI oversold",
                    )
                )

        if (
            sma50[i - 1] is not None
            and sma200[i - 1] is not None
            and sma50[i] is not None
            and sma200[i] is not None
        ):
            if sma50[i - 1] <= sma200[i - 1] and sma50[i] > sma200[i]:
                signals.append(
                    SignalPoint(
                        t=bar.t,
                        type="ma_golden_cross",
                        price=bar.c,
                        direction="bullish",
                        strength=0.9,
                        label="Golden cross",
                    )
                )
            elif sma50[i - 1] >= sma200[i - 1] and sma50[i] < sma200[i]:
                signals.append(
                    SignalPoint(
                        t=bar.t,
                        type="ma_death_cross",
                        price=bar.c,
                        direction="bearish",
                        strength=0.9,
                        label="Death cross",
                    )
                )

        if volume_sma20[i] is not None and bar.v > (volume_sma20[i] * 1.8):
            signals.append(
                SignalPoint(
                    t=bar.t,
                    type="high_volume_spike",
                    price=bar.c,
                    direction="neutral",
                    strength=0.65,
                    label="High volume spike",
                )
            )

    return signals


def derive_levels(bars: list[RawBar]) -> LevelsBlock:
    if not bars:
        return LevelsBlock()

    recent = bars[-60:] if len(bars) >= 60 else bars
    support_price = min(b.l for b in recent)
    resistance_price = max(b.h for b in recent)
    latest = bars[-1]

    pivot_price = (latest.h + latest.l + latest.c) / 3

    pivot = PivotClassic(
        p=round(pivot_price, 4),
        r1=round((2 * pivot_price) - latest.l, 4),
        s1=round((2 * pivot_price) - latest.h, 4),
        r2=round(pivot_price + (latest.h - latest.l), 4),
        s2=round(pivot_price - (latest.h - latest.l), 4),
        r3=round(latest.h + 2 * (pivot_price - latest.l), 4),
        s3=round(latest.l - 2 * (latest.h - pivot_price), 4),
    )

    return LevelsBlock(
        support=[
            PriceLevel(
                price=round(support_price, 4),
                strength=0.75,
                label="Recent support",
                source="recent_lows",
            )
        ],
        resistance=[
            PriceLevel(
                price=round(resistance_price, 4),
                strength=0.75,
                label="Recent resistance",
                source="recent_highs",
            )
        ],
        pivot=PivotBlock(classic=pivot),
    )


def _normalize_run_action(value: Optional[str]) -> Optional[str]:
    if not value:
        return None

    normalized = str(value).strip().lower()
    mapping = {
        "buy": "buy",
        "sell": "sell",
        "hold": "hold",
        "review": "hold",
        "overweight": "buy",
        "underweight": "sell",
    }
    return mapping.get(normalized)


def _merge_run_analysis(
    analysis: AnalysisBlock,
    artifacts: ArtifactsBlock,
    run_id: Optional[str],
) -> tuple[AnalysisBlock, ArtifactsBlock]:
    if not run_id:
        return analysis, artifacts

    try:
        run = get_run(run_id)
    except Exception:
        return analysis, artifacts

    if not run:
        return analysis, artifacts

    result = run.get("result") or {}
    partial = run.get("partial_result") or {}
    decision = result.get("decision") or {}
    reports = result.get("reports") if isinstance(result.get("reports"), dict) else {}

    action = _normalize_run_action(
        result.get("action")
        or decision.get("action")
        or partial.get("action")
    )
    confidence = result.get("confidence")
    if confidence is None:
        confidence = partial.get("confidence")
    summary = result.get("summary") or partial.get("summary")

    if action in {"buy", "sell", "hold"}:
        analysis.decision = action  # type: ignore[assignment]

    if isinstance(confidence, (int, float)):
        analysis.confidence = round(float(confidence), 2)

    if isinstance(summary, str) and summary.strip():
        analysis.summary = summary.strip()

    artifacts.run_id = run_id
    artifacts.decision_available = bool(action or summary)
    artifacts.reports_available = (
        sorted(reports.keys())
        if reports
        else list(partial.get("reports_ready") or [])
    )

    return analysis, artifacts


def derive_analysis(
    bars: list[RawBar],
    sma20: list[Optional[float]],
    sma50: list[Optional[float]],
    sma200: list[Optional[float]],
    rsi14: list[Optional[float]],
    macd_points: list[dict[str, Optional[float]]],
    atr14: list[Optional[float]],
    run_id: Optional[str] = None,
) -> tuple[AnalysisBlock, ArtifactsBlock]:
    if not bars:
        return AnalysisBlock(), ArtifactsBlock()

    last = bars[-1]
    last_sma50 = sma50[-1] if sma50 else None
    last_sma200 = sma200[-1] if sma200 else None
    last_rsi = rsi14[-1] if rsi14 else None
    last_macd = macd_points[-1] if macd_points else {"macd": None, "signal": None, "histogram": None}
    last_atr = atr14[-1] if atr14 else None

    trend = "neutral"
    if last_sma50 is not None and last_sma200 is not None:
        if last.c > last_sma50 > last_sma200:
            trend = "bullish"
        elif last.c < last_sma50 < last_sma200:
            trend = "bearish"

    momentum = "flat"
    if last_macd["macd"] is not None and last_macd["signal"] is not None:
        if last_macd["macd"] > last_macd["signal"]:
            momentum = "improving"
        elif last_macd["macd"] < last_macd["signal"]:
            momentum = "weakening"

    volatility = "moderate"
    if last_atr is not None:
        atr_ratio = last_atr / last.c if last.c else 0
        if atr_ratio < 0.015:
            volatility = "low"
        elif atr_ratio > 0.035:
            volatility = "high"

    decision = "hold"
    confidence = 0.55

    bullish_factors: list[str] = []
    bearish_factors: list[str] = []

    if trend == "bullish":
        bullish_factors.append("Price is trading above medium- and long-term trend levels")
        decision = "buy"
        confidence += 0.1
    elif trend == "bearish":
        bearish_factors.append("Price is trading below medium- and long-term trend levels")
        decision = "sell"
        confidence += 0.1

    if last_macd["macd"] is not None and last_macd["signal"] is not None:
        if last_macd["macd"] > last_macd["signal"]:
            bullish_factors.append("MACD is above its signal line")
            confidence += 0.05
        else:
            bearish_factors.append("MACD is below its signal line")
            confidence += 0.05

    if last_rsi is not None:
        if last_rsi >= 70:
            bearish_factors.append("RSI is in overbought territory")
            confidence -= 0.03
        elif last_rsi <= 30:
            bullish_factors.append("RSI is in oversold territory")
            confidence += 0.03

    confidence = max(0.0, min(1.0, confidence))

    analysis = AnalysisBlock(
        decision=decision,  # type: ignore[arg-type]
        confidence=round(confidence, 2),
        timeframe="swing",
        trend=trend,  # type: ignore[arg-type]
        momentum=momentum,  # type: ignore[arg-type]
        volatility=volatility,  # type: ignore[arg-type]
        risk_level="medium",
        summary="Derived from backend technical indicators; can be replaced or enriched by TradingAgents output.",
        bullish_factors=bullish_factors,
        bearish_factors=bearish_factors,
        trade_levels=TradeLevels(
            entry=round(last.c, 2),
            stop_loss=round(last.c * 0.96, 2),
            take_profit=round(last.c * 1.08, 2),
            risk_reward_ratio=2.0,
        ),
    )

    artifacts = ArtifactsBlock(
        run_id=run_id,
        decision_available=run_id is not None,
        reports_available=["technical", "risk", "final"] if run_id else [],
    )

    analysis, artifacts = _merge_run_analysis(analysis, artifacts, run_id)
    return analysis, artifacts


def build_chart_response(
    symbol: str,
    range_str: str = "3y",
    interval: str = "1d",
    include_indicators: bool = True,
    include_signals: bool = True,
    include_analysis: bool = True,
    run_id: Optional[str] = None,
) -> ChartResponse:
    warnings: list[str] = []
    analysis_date = _resolve_analysis_date_for_chart(run_id)

    try:
        raw_bars = fetch_historical_bars(
            symbol=symbol,
            range_str=range_str,
            interval=interval,
            analysis_date=analysis_date,
        )
    except Exception as exc:
        return ChartResponse(
            symbol=symbol.upper(),
            name=symbol.upper(),
            exchange=None,
            currency="USD",
            request=RequestBlock(
                range_requested=range_str,
                interval_requested=interval,
                include_indicators=include_indicators,
                include_signals=include_signals,
                include_analysis=include_analysis,
            ),
            meta=MetaBlock(
                range_returned=range_str,
                interval=interval,
                has_full_range=False,
                first_available_date="",
                last_available_date="",
                bars_count=0,
                timezone="UTC",
                source="market_data",
                generated_at=iso_now(),
            ),
            bars=[],
            indicators=IndicatorsBlock(),
            series=PlotSeriesBlock(),
            levels=LevelsBlock(),
            signals=[],
            analysis=None,
            artifacts=None,
            warnings=[f"Historical data unavailable: {exc}"],
        )

    if not raw_bars:
        return ChartResponse(
            symbol=symbol.upper(),
            name=symbol.upper(),
            exchange=None,
            currency="USD",
            request=RequestBlock(
                range_requested=range_str,
                interval_requested=interval,
                include_indicators=include_indicators,
                include_signals=include_signals,
                include_analysis=include_analysis,
            ),
            meta=MetaBlock(
                range_returned=range_str,
                interval=interval,
                has_full_range=False,
                first_available_date="",
                last_available_date="",
                bars_count=0,
                timezone="UTC",
                source="market_data",
                generated_at=iso_now(),
            ),
            bars=[],
            indicators=IndicatorsBlock(),
            series=PlotSeriesBlock(),
            levels=LevelsBlock(),
            signals=[],
            analysis=None,
            artifacts=None,
            warnings=["No historical data available for symbol."],
        )

    close_values = closes(raw_bars)
    volume_values = volumes(raw_bars)

    indicators = IndicatorsBlock()
    series = PlotSeriesBlock(
        price=build_plot_price_points(raw_bars),
        volume=build_plot_volume_points(raw_bars),
    )
    signals: list[SignalPoint] = []
    levels = derive_levels(raw_bars)
    analysis: Optional[AnalysisBlock] = None
    artifacts: Optional[ArtifactsBlock] = None

    sma20: list[Optional[float]] = []
    sma50: list[Optional[float]] = []
    sma200: list[Optional[float]] = []
    ema12: list[Optional[float]] = []
    ema26: list[Optional[float]] = []
    rsi14: list[Optional[float]] = []
    atr14: list[Optional[float]] = []
    volume_sma20: list[Optional[float]] = []
    macd_points_raw: list[dict[str, Optional[float]]] = []

    if include_indicators or include_signals or include_analysis:
        sma20 = sma(close_values, 20)
        sma50 = sma(close_values, 50)
        sma200 = sma(close_values, 200)

        ema12 = ema(close_values, 12)
        ema26 = ema(close_values, 26)

        rsi14 = rsi(close_values, 14)
        macd_points_raw = macd(close_values, 12, 26, 9)
        boll20 = bollinger(close_values, 20, 2.0)
        atr14 = atr(raw_bars, 14)
        stoch14_3 = stochastic(raw_bars, 14, 3)
        obv_values = obv(raw_bars)
        volume_sma20 = sma(volume_values, 20)

        if include_indicators:
            indicators.sma = {
                "20": build_time_values(raw_bars, sma20),
                "50": build_time_values(raw_bars, sma50),
                "200": build_time_values(raw_bars, sma200),
            }
            indicators.ema = {
                "12": build_time_values(raw_bars, ema12),
                "26": build_time_values(raw_bars, ema26),
            }
            indicators.rsi = {
                "14": build_time_values(raw_bars, rsi14),
            }
            indicators.macd = {
                "12_26_9": [
                    MacdPoint(
                        t=bar.t,
                        macd=round(point["macd"], 4) if point["macd"] is not None else None,
                        signal=round(point["signal"], 4) if point["signal"] is not None else None,
                        histogram=round(point["histogram"], 4) if point["histogram"] is not None else None,
                    )
                    for bar, point in zip(raw_bars, macd_points_raw, strict=False)
                ]
            }
            indicators.bollinger = {
                "20_2": [
                    BollingerPoint(
                        t=bar.t,
                        middle=round(point["middle"], 4) if point["middle"] is not None else None,
                        upper=round(point["upper"], 4) if point["upper"] is not None else None,
                        lower=round(point["lower"], 4) if point["lower"] is not None else None,
                    )
                    for bar, point in zip(raw_bars, boll20, strict=False)
                ]
            }
            indicators.atr = {
                "14": build_time_values(raw_bars, atr14),
            }
            indicators.stochastic = {
                "14_3": [
                    StochasticPoint(
                        t=bar.t,
                        k=round(point["k"], 4) if point["k"] is not None else None,
                        d=round(point["d"], 4) if point["d"] is not None else None,
                    )
                    for bar, point in zip(raw_bars, stoch14_3, strict=False)
                ]
            }
            indicators.obv = build_time_values(raw_bars, obv_values)
            indicators.volume_sma = {
                "20": build_time_values(raw_bars, volume_sma20),
            }

            series.overlays.sma20 = build_plot_value_points(raw_bars, sma20)
            series.overlays.sma50 = build_plot_value_points(raw_bars, sma50)
            series.overlays.sma200 = build_plot_value_points(raw_bars, sma200)
            series.overlays.ema12 = build_plot_value_points(raw_bars, ema12)
            series.overlays.ema26 = build_plot_value_points(raw_bars, ema26)
            series.overlays.bollinger = build_plot_bollinger_series(raw_bars, boll20)

            series.subcharts.rsi14 = build_plot_value_points(raw_bars, rsi14)
            series.subcharts.macd = build_plot_macd_series(raw_bars, macd_points_raw)
            series.subcharts.atr14 = build_plot_value_points(raw_bars, atr14)
            series.subcharts.stochastic_14_3 = build_plot_stochastic_series(raw_bars, stoch14_3)
            series.subcharts.obv = build_plot_value_points(raw_bars, obv_values)
            series.subcharts.volume_sma20 = build_plot_value_points(raw_bars, volume_sma20)

        if include_signals:
            signals = detect_signals(
                bars=raw_bars,
                rsi14=rsi14,
                macd_points=macd_points_raw,
                sma50=sma50,
                sma200=sma200,
                volume_sma20=volume_sma20,
            )

        if include_analysis:
            analysis, artifacts = derive_analysis(
                bars=raw_bars,
                sma20=sma20,
                sma50=sma50,
                sma200=sma200,
                rsi14=rsi14,
                macd_points=macd_points_raw,
                atr14=atr14,
                run_id=run_id,
            )

    if run_id and analysis_date:
        warnings.append(f"Chart aligned to run analysis date {analysis_date}.")

    return ChartResponse(
        symbol=symbol.upper(),
        name=symbol.upper(),
        exchange="UNKNOWN",
        currency="USD",
        request=RequestBlock(
            range_requested=range_str,
            interval_requested=interval,
            include_indicators=include_indicators,
            include_signals=include_signals,
            include_analysis=include_analysis,
        ),
        meta=MetaBlock(
            range_returned=range_str,
            interval=interval,
            has_full_range=True,
            first_available_date=raw_bars[0].t,
            last_available_date=raw_bars[-1].t,
            bars_count=len(raw_bars),
            timezone="UTC",
            source="market_data",
            generated_at=iso_now(),
        ),
        bars=to_price_bars(raw_bars),
        indicators=indicators,
        series=series,
        levels=levels,
        signals=signals,
        analysis=analysis,
        artifacts=artifacts,
        warnings=warnings,
    )