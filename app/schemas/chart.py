from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class TimeValue(BaseModel):
    t: str
    value: Optional[float] = None


class PriceBar(BaseModel):
    t: str
    o: float
    h: float
    l: float
    c: float
    v: float


class MacdPoint(BaseModel):
    t: str
    macd: Optional[float] = None
    signal: Optional[float] = None
    histogram: Optional[float] = None


class BollingerPoint(BaseModel):
    t: str
    middle: Optional[float] = None
    upper: Optional[float] = None
    lower: Optional[float] = None


class StochasticPoint(BaseModel):
    t: str
    k: Optional[float] = None
    d: Optional[float] = None


class PivotClassic(BaseModel):
    p: float
    r1: float
    s1: float
    r2: float
    s2: float
    r3: float
    s3: float


class PivotBlock(BaseModel):
    classic: Optional[PivotClassic] = None


class PriceLevel(BaseModel):
    price: float
    strength: float
    label: str
    source: str


class LevelsBlock(BaseModel):
    support: List[PriceLevel] = Field(default_factory=list)
    resistance: List[PriceLevel] = Field(default_factory=list)
    pivot: Optional[PivotBlock] = None


class TradeLevels(BaseModel):
    entry: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    risk_reward_ratio: Optional[float] = None


class AnalysisBlock(BaseModel):
    decision: Optional[Literal["buy", "sell", "hold"]] = None
    confidence: Optional[float] = None
    timeframe: Optional[str] = None
    trend: Optional[Literal["bullish", "bearish", "neutral"]] = None
    momentum: Optional[Literal["improving", "weakening", "flat"]] = None
    volatility: Optional[Literal["low", "moderate", "high"]] = None
    risk_level: Optional[str] = None
    summary: Optional[str] = None
    bullish_factors: List[str] = Field(default_factory=list)
    bearish_factors: List[str] = Field(default_factory=list)
    trade_levels: Optional[TradeLevels] = None


class ArtifactsBlock(BaseModel):
    run_id: Optional[str] = None
    decision_available: bool = False
    reports_available: List[str] = Field(default_factory=list)


class SignalPoint(BaseModel):
    t: str
    type: str
    price: Optional[float] = None
    direction: Optional[str] = None
    strength: Optional[float] = None
    label: Optional[str] = None


class RequestBlock(BaseModel):
    range_requested: str
    interval_requested: str
    include_indicators: bool
    include_signals: bool
    include_analysis: bool


class MetaBlock(BaseModel):
    range_returned: str
    interval: str
    has_full_range: bool
    first_available_date: str
    last_available_date: str
    bars_count: int
    timezone: str
    source: str
    generated_at: str


class IndicatorsBlock(BaseModel):
    sma: Dict[str, List[TimeValue]] = Field(default_factory=dict)
    ema: Dict[str, List[TimeValue]] = Field(default_factory=dict)
    rsi: Dict[str, List[TimeValue]] = Field(default_factory=dict)
    macd: Dict[str, List[MacdPoint]] = Field(default_factory=dict)
    bollinger: Dict[str, List[BollingerPoint]] = Field(default_factory=dict)
    atr: Dict[str, List[TimeValue]] = Field(default_factory=dict)
    stochastic: Dict[str, List[StochasticPoint]] = Field(default_factory=dict)
    obv: List[TimeValue] = Field(default_factory=list)
    volume_sma: Dict[str, List[TimeValue]] = Field(default_factory=dict)


# Frontend-friendly plot series models

class PlotValuePoint(BaseModel):
    time: str
    value: Optional[float] = None


class PlotPricePoint(BaseModel):
    time: str
    open: float
    high: float
    low: float
    close: float


class PlotMacdSeries(BaseModel):
    macd: List[PlotValuePoint] = Field(default_factory=list)
    signal: List[PlotValuePoint] = Field(default_factory=list)
    histogram: List[PlotValuePoint] = Field(default_factory=list)


class PlotBollingerSeries(BaseModel):
    upper: List[PlotValuePoint] = Field(default_factory=list)
    middle: List[PlotValuePoint] = Field(default_factory=list)
    lower: List[PlotValuePoint] = Field(default_factory=list)


class PlotStochasticSeries(BaseModel):
    k: List[PlotValuePoint] = Field(default_factory=list)
    d: List[PlotValuePoint] = Field(default_factory=list)


class PlotOverlaySeries(BaseModel):
    sma20: List[PlotValuePoint] = Field(default_factory=list)
    sma50: List[PlotValuePoint] = Field(default_factory=list)
    sma200: List[PlotValuePoint] = Field(default_factory=list)
    ema12: List[PlotValuePoint] = Field(default_factory=list)
    ema26: List[PlotValuePoint] = Field(default_factory=list)
    bollinger: Optional[PlotBollingerSeries] = None


class PlotSubchartSeries(BaseModel):
    rsi14: List[PlotValuePoint] = Field(default_factory=list)
    macd: Optional[PlotMacdSeries] = None
    atr14: List[PlotValuePoint] = Field(default_factory=list)
    stochastic_14_3: Optional[PlotStochasticSeries] = None
    obv: List[PlotValuePoint] = Field(default_factory=list)
    volume_sma20: List[PlotValuePoint] = Field(default_factory=list)


class PlotSeriesBlock(BaseModel):
    price: List[PlotPricePoint] = Field(default_factory=list)
    volume: List[PlotValuePoint] = Field(default_factory=list)
    overlays: PlotOverlaySeries = Field(default_factory=PlotOverlaySeries)
    subcharts: PlotSubchartSeries = Field(default_factory=PlotSubchartSeries)


class ChartResponse(BaseModel):
    symbol: str
    name: str
    exchange: Optional[str] = None
    currency: Optional[str] = None
    request: RequestBlock
    meta: MetaBlock
    bars: List[PriceBar] = Field(default_factory=list)
    indicators: IndicatorsBlock = Field(default_factory=IndicatorsBlock)
    series: PlotSeriesBlock = Field(default_factory=PlotSeriesBlock)
    levels: LevelsBlock = Field(default_factory=LevelsBlock)
    signals: List[SignalPoint] = Field(default_factory=list)
    analysis: Optional[AnalysisBlock] = None
    artifacts: Optional[ArtifactsBlock] = None
    warnings: List[str] = Field(default_factory=list)