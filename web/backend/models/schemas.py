"""Pydantic models — 7 primary × 16 secondary indicator system, 100-point scale."""

from typing import Optional
from pydantic import BaseModel, Field, field_validator


# ── Secondary indicator result ─────────────────────────────────────
class SecondaryResult(BaseModel):
    """A single secondary indicator evaluation result."""

    id: str = Field(..., description="二级指标编号，如 '1.1', '3.2'", examples=["1.1"])
    name: str = Field(..., description="二级指标名称", examples=["背景描述"])
    max_score: int = Field(..., description="该指标满分")
    score: int = Field(..., ge=0, description="实际得分")
    evidence: str = Field(default="", max_length=500, description="提取的证据")
    comment: str = Field(default="", max_length=200, description="简短评语")

    @field_validator("score", mode="before")
    @classmethod
    def coerce_score(cls, v):
        if isinstance(v, float):
            return int(round(v))
        return v


# ── Primary indicator result (aggregated) ──────────────────────────
class PrimaryResult(BaseModel):
    """Aggregated result for a primary indicator (sum of secondary scores)."""

    id: str = Field(..., description="一级指标编号，如 '一', '二'", examples=["一"])
    name: str = Field(..., description="一级指标名称", examples=["研究背景与问题界定"])
    weight: int = Field(..., description="该一级指标满分")
    score: int = Field(..., ge=0, description="实际得分")
    secondary_results: list[SecondaryResult] = Field(
        default_factory=list, description="下属二级指标结果"
    )


# ── Additional item result ─────────────────────────────────────────
class AdditionalResult(BaseModel):
    """Optional additional item (±5 points)."""

    name: str = Field(default="学科适配性")
    score: int = Field(default=0, ge=-5, le=5, description="附加分，范围 -5 到 +5")
    comment: str = Field(default="", max_length=200, description="附加项评语")


# ── Evaluation request ─────────────────────────────────────────────
class EvaluationRequest(BaseModel):
    """Request to evaluate a document."""

    text: str = Field(..., min_length=1, description="文献全文或截断后的文本")
    doc_name: str = Field(default="未命名文献", max_length=255, description="文件名")


# ── Evaluation response ────────────────────────────────────────────
class EvaluationResponse(BaseModel):
    """Full evaluation response with all primary and secondary results."""

    id: str = Field(..., description="评价记录唯一标识")
    doc_name: str = Field(..., description="文献名称")
    timestamp: str = Field(..., description="评价时间")
    total_score: float = Field(..., ge=0.0, le=110.0, description="总分（含附加分，已缩放至百分制）")
    base_score: float = Field(..., ge=0.0, le=100.0, description="基础分（已缩放至100分制）")
    scale_factor: float = Field(
        default=1.0, description="缩放系数（原始分 × scale_factor → 百分制）"
    )
    excluded_indicators: list[str] = Field(
        default_factory=list, description="被排除的二级指标ID列表"
    )
    primary_results: list[PrimaryResult] = Field(
        ..., description="一级指标评价结果（6或7项）"
    )
    additional_results: list[AdditionalResult] = Field(
        default_factory=list, description="附加项结果列表（学科适配性、方法学复杂度、政策时效性）"
    )
    overall_comment: str = Field(default="", description="综合评价结语")

    model_config = {"json_schema_extra": {
        "example": {
            "id": "20260511143000",
            "doc_name": "中医药发展报告.pdf",
            "timestamp": "2026-05-11 14:30:00",
            "total_score": 82.5,
            "base_score": 80.5,
            "primary_results": [
                {
                    "id": "一",
                    "name": "研究背景与问题界定",
                    "weight": 15,
                    "score": 12,
                    "secondary_results": [
                        {"id": "1.1", "name": "背景描述", "max_score": 8, "score": 6, "evidence": "...", "comment": "良好"},
                        {"id": "1.2", "name": "问题界定", "max_score": 7, "score": 6, "evidence": "...", "comment": "优秀"},
                    ],
                }
            ],
            "additional_result": {"name": "学科适配性", "score": 2, "comment": "中医药特色突出"},
            "overall_comment": "该文献质量良好...",
        }
    }}


# ── History ────────────────────────────────────────────────────────
class HistoryRecord(BaseModel):
    """Persisted evaluation record (backward-compatible)."""

    id: str = Field(..., description="唯一标识")
    timestamp: str = Field(..., description="评价时间")
    doc_name: str = Field(..., description="文献名称")
    ip_address: str = Field(default="unknown", description="客户端 IP")
    total_score: float = Field(..., description="总分")
    base_score: float = Field(default=0.0, description="基础分")
    scale_factor: float = Field(default=1.0, description="缩放系数")
    excluded_indicators: list[str] = Field(default_factory=list, description="被排除的二级指标ID")
    primary_results: list[PrimaryResult] = Field(
        default_factory=list, description="一级指标结果"
    )
    additional_results: list[AdditionalResult] = Field(default_factory=list)
    overall_comment: str = Field(default="", description="综合评价结语")


class HistoryListResponse(BaseModel):
    """History list wrapper."""

    total: int = Field(..., description="总记录数")
    items: list[HistoryRecord] = Field(..., description="历史记录列表")


class IpStat(BaseModel):
    """Per-IP usage statistics."""

    ip_address: str = Field(..., description="IP 地址")
    count: int = Field(..., description="评价次数")
    last_seen: str = Field(..., description="最近活跃时间")


class IpListResponse(BaseModel):
    """IP list wrapper."""

    total: int = Field(..., description="不同 IP 数量")
    items: list[IpStat] = Field(..., description="IP 统计列表")


# ── Error ──────────────────────────────────────────────────────────
# ── Batch evaluation ────────────────────────────────────────────────
class BatchFileResult(BaseModel):
    """Evaluation result for a single file within a batch."""

    filename: str = Field(..., description="文件名")
    status: str = Field(..., description="处理状态: success / error")
    text_length: int = Field(default=0, description="解析出的文本长度")
    result: Optional[EvaluationResponse] = Field(default=None, description="评价结果")
    error: str = Field(default="", description="错误信息（仅 status=error 时）")


class BatchEvaluationResponse(BaseModel):
    """Response for batch evaluation (up to 5 files)."""

    total: int = Field(..., description="上传文件总数")
    success_count: int = Field(..., description="成功评价数量")
    error_count: int = Field(..., description="失败数量")
    results: list[BatchFileResult] = Field(
        default_factory=list, description="各文件评价结果"
    )


# ── Error ──────────────────────────────────────────────────────────
class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str = Field(..., description="错误类型")
    message: str = Field(..., description="错误详情")
    detail: Optional[str] = Field(default=None, description="详细错误信息")
