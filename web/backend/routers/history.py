"""History endpoints — browse, filter by username, and manage past evaluations."""

import logging

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from backend.models.schemas import (
    HistoryListResponse,
    HistoryRecord,
    ErrorResponse,
    EvaluationResponse,
    IpStat,
    IpListResponse,
)
from backend.services.history_service import (
    list_history,
    get_record,
    delete_record,
    get_ip_list,
    get_total_count,
)
from backend.utils.report_generator import (
    generate_report,
    get_mime_type,
    get_file_extension,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/history", tags=["history"])


@router.get(
    "",
    response_model=HistoryListResponse,
    summary="获取历史记录列表",
    description="返回最近的评价历史记录，支持按用户名筛选，按时间倒序排列。",
)
def get_history_list(
    limit: int = Query(20, ge=1, le=100, description="返回记录数"),
    username: str = Query("", description="按用户名筛选"),
) -> HistoryListResponse:
    """List recent evaluation records, optionally filtered by username."""
    user_filter = username.strip() if username.strip() else None
    items_raw = list_history(limit=limit, username=user_filter)
    items = [HistoryRecord(**item) for item in items_raw]
    return HistoryListResponse(
        total=len(items),
        items=items,
    )


@router.get(
    "/{record_id}",
    response_model=HistoryRecord,
    responses={404: {"model": ErrorResponse}},
    summary="获取单条历史记录",
    description="根据记录 ID 获取完整的评价结果。",
)
def get_history_detail(record_id: str) -> HistoryRecord:
    """Retrieve a single evaluation record by ID."""
    record = get_record(record_id)
    if record is None:
        raise HTTPException(
            status_code=404, detail=f"记录 {record_id} 不存在"
        )
    return HistoryRecord(**record)


@router.get(
    "/{record_id}/report",
    responses={404: {"model": ErrorResponse}},
    summary="下载历史评价报告",
    description="根据记录 ID 生成并下载指定格式（docx/pdf/txt）的评价报告。",
)
def download_history_report(
    record_id: str,
    fmt: str = Query("docx", description="报告格式: docx, pdf, txt"),
) -> Response:
    """Generate a downloadable report from a saved evaluation record."""
    if fmt not in ("docx", "pdf", "txt"):
        raise HTTPException(status_code=400, detail=f"不支持的格式 '{fmt}'，可选 docx/pdf/txt")

    record = get_record(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"记录 {record_id} 不存在")

    response = EvaluationResponse(
        id=record["id"],
        doc_name=record["doc_name"],
        timestamp=record["timestamp"],
        total_score=record["total_score"],
        base_score=record["base_score"],
        scale_factor=record.get("scale_factor", 1.0),
        excluded_indicators=record.get("excluded_indicators", []),
        primary_results=record["primary_results"],
        additional_results=record["additional_results"],
        overall_comment=record.get("overall_comment", ""),
    )

    report_bytes = generate_report(response, fmt)
    base_name = record["doc_name"].rsplit(".", 1)[0]

    from urllib.parse import quote
    filename = f"评价报告_{base_name}{get_file_extension(fmt)}"
    return Response(
        content=report_bytes,
        media_type=get_mime_type(fmt),
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )


@router.delete(
    "/{record_id}",
    responses={200: {"description": "删除成功"}, 404: {"model": ErrorResponse}},
    summary="删除历史记录",
    description="根据记录 ID 删除一条评价历史。",
)
def delete_history(record_id: str) -> dict:
    """Delete a single evaluation record."""
    ok = delete_record(record_id)
    if not ok:
        raise HTTPException(
            status_code=404, detail=f"记录 {record_id} 不存在"
        )
    return {"status": "deleted", "id": record_id}


# ── IP statistics ────────────────────────────────────────────────
@router.get(
    "/ips/list",
    response_model=IpListResponse,
    summary="获取 IP 统计列表",
    description="返回所有使用过本系统的客户端 IP 及其评价次数。",
)
def get_ips() -> IpListResponse:
    """List distinct IPs with evaluation counts."""
    items_raw = get_ip_list()
    items = [IpStat(**r) for r in items_raw]
    return IpListResponse(total=len(items), items=items)


@router.get(
    "/count",
    summary="获取评价总次数",
    description="返回评价总次数，可按用户名筛选。",
)
def get_count(
    username: str = Query("", description="按用户名筛选"),
) -> dict:
    """Return total evaluation count."""
    user_filter = username.strip() if username.strip() else None
    return {"count": get_total_count(username=user_filter)}


@router.get(
    "/by-user/{username}",
    response_model=HistoryListResponse,
    summary="按用户名查询历史记录",
    description="根据用户名查询该用户的所有评价记录。",
)
def get_history_by_user(
    username: str,
    limit: int = Query(50, ge=1, le=100),
) -> HistoryListResponse:
    """List evaluations from a specific user."""
    items_raw = list_history(limit=limit, username=username)
    items = [HistoryRecord(**item) for item in items_raw]
    return HistoryListResponse(total=len(items), items=items)
