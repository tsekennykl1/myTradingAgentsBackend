from typing import Any, Dict, List, Literal, Optional

import pandas as pd
from pydantic import BaseModel, Field, field_validator


class RunConfig(BaseModel):
    model: str = Field(..., min_length=1, description="Model or engine name")
    ticker: Optional[str] = Field(default=None, min_length=1)
    tickers: List[str] = Field(default_factory=list)
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    interval: Optional[Literal["1m", "5m", "15m", "1h", "1d", "1wk", "1mo"]] = None
    strategy: Optional[str] = None
    initial_capital: Optional[float] = Field(default=None, gt=0)
    params: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("tickers")
    @classmethod
    def validate_tickers(cls, value: List[str]) -> List[str]:
        return [ticker.strip().upper() for ticker in value if ticker and ticker.strip()]

    @field_validator("ticker")
    @classmethod
    def validate_ticker(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        return value.strip().upper()

    @field_validator("end_date")
    @classmethod
    def validate_end_date_present_format(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        pd.to_datetime(value)
        return value

    @field_validator("start_date")
    @classmethod
    def validate_start_date_present_format(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        pd.to_datetime(value)
        return value