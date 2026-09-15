from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Any

import matplotlib
matplotlib.use("Agg")

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots


HK_PUBLIC_HOLIDAYS_2026 = [
    "2026-01-01",
    "2026-02-17",
    "2026-02-18",
    "2026-02-19",
    "2026-04-03",
    "2026-04-04",
    "2026-04-06",
    "2026-04-07",
    "2026-05-01",
    "2026-05-25",
    "2026-06-19",
    "2026-07-01",
    "2026-09-26",
    "2026-10-01",
    "2026-10-19",
    "2026-12-25",
    "2026-12-26",
]


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _has_col(df: pd.DataFrame, name: str) -> bool:
    return name in df.columns


def _resolve_stock_name(ticker: str, df: pd.DataFrame) -> str:
    if not df.empty and "Stock" in df.columns:
        value = df["Stock"].iloc[-1]
        if pd.notna(value):
            text = str(value).strip()
            if text:
                return text
    return ticker


def _resolve_stock_code(ticker: str, df: pd.DataFrame) -> str:
    if not df.empty and "Stock Code" in df.columns:
        value = df["Stock Code"].iloc[-1]
        if pd.notna(value):
            text = str(value).strip()
            if text:
                return text
    return ticker


def _resolve_rangebreaks(ticker: str) -> list[dict[str, Any]]:
    rangebreaks: list[dict[str, Any]] = [dict(bounds=["sat", "mon"])]
    if ticker.upper().endswith(".HK") or ticker.upper() == "^HSI":
        rangebreaks.append(dict(values=HK_PUBLIC_HOLIDAYS_2026))
    return rangebreaks


def _validate_price_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        raise ValueError("Price DataFrame is empty")

    if not isinstance(df.index, pd.DatetimeIndex):
        if "Date" in df.columns:
            df = df.copy()
            df["Date"] = pd.to_datetime(df["Date"])
            df = df.set_index("Date")
        else:
            raise ValueError("Price DataFrame must use a DatetimeIndex or contain a 'Date' column")

    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Price DataFrame missing required columns: {missing}")

    return df.sort_index()


def _figure_title(ticker: str, df: pd.DataFrame) -> str:
    stock_name = _resolve_stock_name(ticker, df)
    stock_code = _resolve_stock_code(ticker, df)
    start = df.index.min().strftime("%Y-%m-%d")
    end = df.index.max().strftime("%Y-%m-%d")
    return f"<b>{stock_name} {stock_code}</b><br>Share Price from {start} to {end}"


def build_plotly_figure(ticker: str, df: pd.DataFrame) -> go.Figure:
    df = _validate_price_df(df)
    rangebreaks = _resolve_rangebreaks(ticker)

    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.03,
        row_heights=[0.62, 0.18, 0.20],
        subplot_titles=("", "Volume", "RSI"),
    )

    fig.add_trace(
        go.Candlestick(
            x=df.index,
            open=df["Open"],
            high=df["High"],
            low=df["Low"],
            close=df["Close"],
            increasing_line_color="#16a34a",
            decreasing_line_color="#dc2626",
            increasing_fillcolor="#16a34a",
            decreasing_fillcolor="#dc2626",
            name="Candles",
        ),
        row=1,
        col=1,
    )

    overlay_columns = [
        ("SMA20", "#2563eb"),
        ("SMA50", "#7c3aed"),
        ("SMA200", "#475569"),
        ("EMA12", "#f59e0b"),
        ("EMA20", "#f97316"),
        ("EMA26", "#ef4444"),
        ("EMA50", "#22c55e"),
        ("rolling_vwap_20", "#06b6d4"),
        ("VWAP", "#06b6d4"),
    ]

    seen_names = set()
    for col, color in overlay_columns:
        if _has_col(df, col):
            label = "VWAP" if col in {"rolling_vwap_20", "VWAP"} else col
            if label in seen_names:
                continue
            seen_names.add(label)
            fig.add_trace(
                go.Scatter(
                    x=df.index,
                    y=df[col],
                    name=label,
                    mode="lines",
                    line=dict(color=color, width=1.5),
                ),
                row=1,
                col=1,
            )

    if _has_col(df, "BB Upper"):
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["BB Upper"],
                name="BB Upper",
                mode="lines",
                line=dict(color="rgba(99,102,241,0.55)", width=1),
            ),
            row=1,
            col=1,
        )

    if _has_col(df, "BB Lower"):
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["BB Lower"],
                name="BB Lower",
                mode="lines",
                line=dict(color="rgba(99,102,241,0.55)", width=1),
                fill="tonexty" if _has_col(df, "BB Upper") else None,
                fillcolor="rgba(99,102,241,0.08)" if _has_col(df, "BB Upper") else None,
            ),
            row=1,
            col=1,
        )

    volume_colors = [
        "#16a34a" if close >= open_ else "#dc2626"
        for open_, close in zip(df["Open"], df["Close"])
    ]
    fig.add_trace(
        go.Bar(
            x=df.index,
            y=df["Volume"],
            name="Volume",
            marker=dict(color=volume_colors),
            showlegend=False,
        ),
        row=2,
        col=1,
    )

    if _has_col(df, "Volume MA20"):
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["Volume MA20"],
                name="Volume MA20",
                mode="lines",
                line=dict(color="#1d4ed8", width=1.5),
            ),
            row=2,
            col=1,
        )

    if _has_col(df, "RSI14"):
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=df["RSI14"],
                name="RSI14",
                line=dict(color="#6b7280", width=1.8),
            ),
            row=3,
            col=1,
        )
        fig.add_hline(y=70, line_dash="dash", line_color="#dc2626", row=3, col=1)
        fig.add_hline(y=30, line_dash="dash", line_color="#16a34a", row=3, col=1)
        fig.update_yaxes(title_text="RSI", range=[0, 100], row=3, col=1)
    else:
        fig.add_trace(
            go.Scatter(
                x=df.index,
                y=[None] * len(df.index),
                name="RSI unavailable",
                showlegend=False,
            ),
            row=3,
            col=1,
        )
        fig.update_yaxes(title_text="RSI", row=3, col=1)

    fig.update_layout(
        title=_figure_title(ticker, df),
        yaxis_title="Price",
        xaxis_rangeslider_visible=False,
        width=1200,
        height=820,
        template="plotly_white",
        margin=dict(l=40, r=30, t=80, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.01, xanchor="left", x=0),
        hovermode="x unified",
    )

    for row in (1, 2, 3):
        fig.update_xaxes(rangebreaks=rangebreaks, row=row, col=1)

    fig.update_yaxes(title_text="Volume", row=2, col=1)

    return fig


def _payload_to_df(payload: dict[str, Any]) -> tuple[str, pd.DataFrame]:
    rows = payload.get("rows") or []
    ticker = str(payload.get("ticker") or "UNKNOWN")

    if not rows:
        raise ValueError("price-chart payload has no rows")

    df = pd.DataFrame(rows).copy()
    df["Date"] = pd.to_datetime(df["date"])
    df = df.set_index("Date")

    rename_map = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
        "volume_ma20": "Volume MA20",
        "sma20": "SMA20",
        "sma50": "SMA50",
        "sma200": "SMA200",
        "ema12": "EMA12",
        "ema20": "EMA20",
        "ema26": "EMA26",
        "ema50": "EMA50",
        "rsi14": "RSI14",
        "bb_upper": "BB Upper",
        "bb_middle": "BB Middle",
        "bb_lower": "BB Lower",
        "rolling_vwap_20": "rolling_vwap_20",
        "vwap": "rolling_vwap_20",
    }
    df = df.rename(columns=rename_map)

    meta = payload.get("meta") or {}
    stock_name = meta.get("stock_name")
    stock_code = meta.get("stock_code")

    if stock_name and "Stock" not in df.columns:
        df["Stock"] = stock_name
    if stock_code and "Stock Code" not in df.columns:
        df["Stock Code"] = stock_code

    return ticker, df


def render_price_chart_html(payload: dict[str, Any]) -> str:
    ticker, df = _payload_to_df(payload)
    fig = build_plotly_figure(ticker, df)
    return fig.to_html(full_html=True, include_plotlyjs="cdn")


def _build_matplotlib_png_bytes(ticker: str, df: pd.DataFrame) -> bytes:
    df = _validate_price_df(df)

    stock_name = _resolve_stock_name(ticker, df)
    stock_code = _resolve_stock_code(ticker, df)

    fig, (ax1, ax2, ax3) = plt.subplots(
        3,
        1,
        figsize=(14, 9),
        sharex=True,
        gridspec_kw={"height_ratios": [3.6, 1.2, 1.2]},
    )

    ax1.plot(df.index, df["Close"], label="Close", color="black", linewidth=1.4)

    plotted_labels = set()
    for col, color, label in [
        ("SMA20", "#2563eb", "SMA20"),
        ("SMA50", "#7c3aed", "SMA50"),
        ("SMA200", "#475569", "SMA200"),
        ("EMA12", "#f59e0b", "EMA12"),
        ("EMA20", "#f97316", "EMA20"),
        ("EMA26", "#ef4444", "EMA26"),
        ("EMA50", "#22c55e", "EMA50"),
        ("rolling_vwap_20", "#06b6d4", "VWAP"),
        ("VWAP", "#06b6d4", "VWAP"),
    ]:
        if _has_col(df, col) and label not in plotted_labels:
            ax1.plot(df.index, df[col], label=label, color=color, linewidth=1.2)
            plotted_labels.add(label)

    if _has_col(df, "BB Upper") and _has_col(df, "BB Lower"):
        ax1.plot(df.index, df["BB Upper"], color="#6366f1", linewidth=1.0, alpha=0.6, label="BB Upper")
        ax1.plot(df.index, df["BB Lower"], color="#6366f1", linewidth=1.0, alpha=0.6, label="BB Lower")
        ax1.fill_between(df.index, df["BB Lower"], df["BB Upper"], color="#6366f1", alpha=0.08)

    ax1.set_title(
        f"{stock_name} {stock_code}\n"
        f"Share Price from {df.index.min().strftime('%Y-%m-%d')} "
        f"to {df.index.max().strftime('%Y-%m-%d')}"
    )
    ax1.set_ylabel("Price")
    ax1.legend(loc="upper left", ncol=4, fontsize=8)
    ax1.grid(True, alpha=0.25)

    bar_colors = ["#16a34a" if c >= o else "#dc2626" for o, c in zip(df["Open"], df["Close"])]
    ax2.bar(df.index, df["Volume"], color=bar_colors, width=1.0)

    if _has_col(df, "Volume MA20"):
        ax2.plot(df.index, df["Volume MA20"], color="#1d4ed8", linewidth=1.4, label="Volume MA20")
        ax2.legend(loc="upper left", fontsize=8)

    ax2.set_ylabel("Volume")
    ax2.grid(True, alpha=0.25)

    if _has_col(df, "RSI14"):
        ax3.plot(df.index, df["RSI14"], color="#6b7280", label="RSI14", linewidth=1.4)
        ax3.axhline(70, linestyle="--", color="#dc2626", alpha=0.8)
        ax3.axhline(30, linestyle="--", color="#16a34a", alpha=0.8)
        ax3.set_ylim(0, 100)
        ax3.legend(loc="upper left", fontsize=8)

    ax3.set_ylabel("RSI")
    ax3.grid(True, alpha=0.25)

    locator = mdates.AutoDateLocator()
    formatter = mdates.ConciseDateFormatter(locator)
    ax3.xaxis.set_major_locator(locator)
    ax3.xaxis.set_major_formatter(formatter)

    plt.tight_layout()

    buf = BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return buf.read()


def render_price_chart_png(payload: dict[str, Any]) -> bytes:
    ticker, df = _payload_to_df(payload)
    return _build_matplotlib_png_bytes(ticker, df)


def save_chart_html(output_dir: Path, fig: go.Figure) -> Path:
    _ensure_dir(output_dir)

    new_path = output_dir / "price-chart.html"
    old_path = output_dir / "chart.html"
    html = fig.to_html(full_html=True, include_plotlyjs="cdn")

    new_path.write_text(html, encoding="utf-8")
    old_path.write_text(html, encoding="utf-8")

    return new_path


def save_chart_png(output_dir: Path, ticker: str, df: pd.DataFrame) -> Path:
    _ensure_dir(output_dir)

    png_bytes = _build_matplotlib_png_bytes(ticker, df)

    new_path = output_dir / "price-chart.png"
    old_path = output_dir / "chart.png"

    new_path.write_bytes(png_bytes)
    old_path.write_bytes(png_bytes)

    return new_path


def build_charts(output_dir: Path, run_id: str, ticker: str, df: pd.DataFrame) -> dict[str, str]:
    _ensure_dir(output_dir)

    fig = build_plotly_figure(ticker, df)
    save_chart_html(output_dir, fig)
    save_chart_png(output_dir, ticker, df)

    return {
        "plotly": f"/runs/{run_id}/price-chart.html",
        "png": f"/runs/{run_id}/price-chart.png",
        "plotly_legacy": f"/runs/{run_id}/chart.html",
        "png_legacy": f"/runs/{run_id}/chart.png",
    }