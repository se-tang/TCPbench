from typing import List, Optional
from pydantic import BaseModel, Field, validator


class SiteResult(BaseModel):
    name: str
    host: str
    avg: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    success: int
    total: int
    loss_pct: float
    samples: List[Optional[float]] = Field(default_factory=list)

    @validator("samples")
    def limit_samples(cls, v):
        if len(v) > 500:
            raise ValueError("samples 数量超出限制")
        return v

    @validator("loss_pct")
    def loss_range(cls, v):
        if not (0 <= v <= 100):
            raise ValueError("loss_pct 必须在 0-100 之间")
        return v


class ReportIn(BaseModel):
    hostname: str = Field(..., max_length=128)
    ip: str = Field(..., max_length=64)
    results: List[SiteResult]

    @validator("results")
    def limit_results(cls, v):
        if len(v) == 0:
            raise ValueError("results 不能为空")
        if len(v) > 200:
            raise ValueError("results 数量超出限制")
        return v


class ReportOut(BaseModel):
    id: str
    url: str
    today_count: int
    total_count: int
