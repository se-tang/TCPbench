from typing import List, Optional
from pydantic import BaseModel, Field, validator


class SiteResult(BaseModel):
    name: str
    host: str
    target_ip: Optional[str] = None
    dns_ms: Optional[float] = None
    supported: bool = True
    candidate_count: int = 0
    avg: Optional[float] = None
    min: Optional[float] = None
    max: Optional[float] = None
    p50: Optional[float] = None
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

    @validator("supported", pre=True, always=True)
    def default_supported(cls, v):
        return True if v is None else v


class ReportIn(BaseModel):
    hostname: str = Field(..., max_length=128)
    ip: str = Field(..., max_length=64)
    measurement_version: int = 1
    ip_family: Optional[str] = None
    batch_id: Optional[str] = None
    owner_token: Optional[str] = None
    dns_server: Optional[str] = Field(None, max_length=255)
    results: List[SiteResult]

    @validator("results")
    def limit_results(cls, v):
        if len(v) == 0:
            raise ValueError("results 不能为空")
        if len(v) > 200:
            raise ValueError("results 数量超出限制")
        # 必须完整跑满 60 轮，防止低轮次刷榜
        for r in v:
            if r.supported and r.total != 60:
                raise ValueError(f"测试无效：站点 {r.name} 轮次为 {r.total}，必须完整跑满 60 轮")
            if not r.supported and (r.total != 0 or r.success != 0):
                raise ValueError(f"不支持当前地址族的站点 {r.name} 不应包含测试轮次")
        return v


class ReportOut(BaseModel):
    id: str
    url: str
    batch_id: Optional[str] = None
    owner_token: Optional[str] = None
    ip_family: Optional[str] = None
    measurement_version: int = 1
    today_count: int
    total_count: int


class MachineClaimIn(BaseModel):
    token: str = Field(..., max_length=128)
    provider: str = Field(..., min_length=2, max_length=64)
    region: str = Field(..., min_length=2, max_length=64)
    plan: str = Field(..., min_length=2, max_length=128)
    note: str = Field("", max_length=200)
