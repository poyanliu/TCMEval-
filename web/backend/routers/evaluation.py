"""Evaluation endpoints — document upload and scoring."""

import hashlib
import logging
from io import BytesIO
from typing import Annotated

from fastapi import APIRouter, Request, UploadFile, File, Form, HTTPException, Query
from fastapi.responses import Response

from backend.models.schemas import (
    EvaluationRequest,
    EvaluationResponse,
    ErrorResponse,
    BatchFileResult,
    BatchEvaluationResponse,
    HistoryRecord,
)
from backend.services.evaluation_service import evaluate_document
from backend.services.history_service import save_to_history, lookup_cached_result
from backend.utils.document_parser import parse_document, detect_format

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/evaluate", tags=["evaluation"])


@router.post(
    "",
    response_model=EvaluationResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    summary="评价文本内容",
    description="对文献进行7项一级指标、16项二级指标的百分制评分。",
)
def evaluate_text(request: EvaluationRequest, req: Request) -> EvaluationResponse:
    """Evaluate a document from raw text."""
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="文献内容不能为空")

    response = evaluate_document(
        text=request.text,
        doc_name=request.doc_name,
    )

    save_to_history(
        record_id=response.id,
        timestamp=response.timestamp,
        doc_name=response.doc_name,
        base_score=response.base_score,
        total_score=response.total_score,
        scale_factor=response.scale_factor,
        excluded_indicators=response.excluded_indicators,
        primary_results=response.primary_results,
        additional_results=response.additional_results,
        overall_comment=response.overall_comment,
        ip_address=req.client.host if req.client else "unknown",
        username="",
    )

    return response


@router.post(
    "/upload",
    response_model=EvaluationResponse,
    responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse}},
    summary="上传文献文件并评价",
    description="上传 PDF 或 DOCX 文件，自动解析后进行百分制评分。",
)
async def evaluate_upload(
    request: Request,
    file: Annotated[UploadFile, File(description="PDF 或 DOCX 文献文件")],
    doc_name: Annotated[str, Form()] = "",
) -> EvaluationResponse:
    """Upload and evaluate a document file."""
    client_ip = request.client.host if request.client else "unknown"

    # Validate format
    filename = file.filename or "unknown"
    mime_type = detect_format(filename)
    if mime_type is None:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式 '{filename}'，仅支持 PDF 和 DOCX",
        )

    # Read file content + compute hash
    content = await file.read()
    file_hash = hashlib.md5(content).hexdigest()
    file_size_mb = len(content) / (1024 * 1024)
    if file_size_mb > 50:
        raise HTTPException(
            status_code=413,
            detail=f"文件过大 ({file_size_mb:.1f} MB)，上限 50 MB",
        )

    # Dedup: check if this file was already evaluated
    cached = lookup_cached_result(file_hash)
    if cached:
        logger.info("Cache hit for %s (hash=%s)", filename, file_hash[:16])
        return HistoryRecord(**cached)

    # Parse document (offload to thread to avoid blocking event loop)
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        name, text = await loop.run_in_executor(
            None, parse_document, BytesIO(content), filename
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.exception("Document parsing failed")
        raise HTTPException(status_code=500, detail=f"文献解析失败: {exc}")

    if not text.strip():
        raise HTTPException(status_code=400, detail="文献内容为空或解析失败")

    # Evaluate
    response = evaluate_document(
        text=text,
        doc_name=doc_name or name,
    )

    save_to_history(
        record_id=response.id,
        timestamp=response.timestamp,
        doc_name=response.doc_name,
        base_score=response.base_score,
        total_score=response.total_score,
        scale_factor=response.scale_factor,
        excluded_indicators=response.excluded_indicators,
        primary_results=response.primary_results,
        additional_results=response.additional_results,
        overall_comment=response.overall_comment,
        ip_address=client_ip,
        username="",
        filename=filename,
        file_hash=file_hash,
    )

    return response


@router.post(
    "/batch",
    response_model=BatchEvaluationResponse,
    responses={400: {"model": ErrorResponse}},
    summary="批量上传文献并评价",
    description="一次性上传最多5个 PDF/DOCX 文件，依次解析并评分。",
)
async def evaluate_batch(
    request: Request,
    files: Annotated[
        list[UploadFile],
        File(description="PDF 或 DOCX 文献文件，最多5个"),
    ],
) -> BatchEvaluationResponse:
    """Batch upload and evaluate up to 5 documents."""
    import asyncio

    client_ip = request.client.host if request.client else "unknown"

    if len(files) > 5:
        raise HTTPException(
            status_code=400,
            detail=f"单次最多上传5个文件，当前收到 {len(files)} 个",
        )

    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个文件")

    loop = asyncio.get_event_loop()
    results: list[BatchFileResult] = []

    for file in files:
        filename = file.filename or "unknown"

        if detect_format(filename) is None:
            results.append(BatchFileResult(
                filename=filename,
                status="error",
                error=f"不支持的文件格式 '{filename}'，仅支持 PDF 和 DOCX",
            ))
            continue

        content = await file.read()
        file_hash = hashlib.md5(content).hexdigest()
        file_size_mb = len(content) / (1024 * 1024)
        if file_size_mb > 50:
            results.append(BatchFileResult(
                filename=filename,
                status="error",
                error=f"文件过大 ({file_size_mb:.1f} MB)，上限 50 MB",
            ))
            continue

        # Dedup: check cache first
        cached = lookup_cached_result(file_hash)
        if cached:
            logger.info("Batch cache hit for %s (hash=%s)", filename, file_hash[:16])
            results.append(BatchFileResult(
                filename=filename,
                status="success",
                text_length=0,
                result=HistoryRecord(**cached),
            ))
            continue

        try:
            name, text = await loop.run_in_executor(
                None, parse_document, BytesIO(content), filename
            )
        except ValueError as exc:
            results.append(BatchFileResult(
                filename=filename, status="error", error=str(exc),
            ))
            continue
        except Exception as exc:
            logger.exception("Batch: parse failed for %s", filename)
            results.append(BatchFileResult(
                filename=filename, status="error", error=f"文献解析失败: {exc}",
            ))
            continue

        if not text.strip():
            results.append(BatchFileResult(
                filename=filename, status="error", error="文献内容为空或解析失败",
            ))
            continue

        try:
            response = await loop.run_in_executor(
                None,
                lambda: evaluate_document(text=text, doc_name=name),
            )
        except Exception as exc:
            logger.exception("Batch: evaluation failed for %s", filename)
            results.append(BatchFileResult(
                filename=filename,
                status="error",
                text_length=len(text),
                error=f"评价失败: {exc}",
            ))
            continue

        save_to_history(
            record_id=response.id,
            timestamp=response.timestamp,
            doc_name=response.doc_name,
            base_score=response.base_score,
            total_score=response.total_score,
            scale_factor=response.scale_factor,
            excluded_indicators=response.excluded_indicators,
            primary_results=response.primary_results,
            additional_results=response.additional_results,
            overall_comment=response.overall_comment,
            ip_address=client_ip,
            filename=filename,
            file_hash=file_hash,
        )

        results.append(BatchFileResult(
            filename=filename,
            status="success",
            text_length=len(text),
            result=response,
        ))

    success_count = sum(1 for r in results if r.status == "success")
    error_count = sum(1 for r in results if r.status == "error")

    return BatchEvaluationResponse(
        total=len(files),
        success_count=success_count,
        error_count=error_count,
        results=results,
    )


# ── Background task queue for async batch evaluation ────────────
import threading
import uuid
from pydantic import BaseModel as PydanticBaseModel

class AsyncBatchResponse(PydanticBaseModel):
    task_id: str
    file_count: int
    filenames: list[str]
    message: str

@router.post(
    "/batch/async",
    response_model=AsyncBatchResponse,
    summary="批量上传并后台评价（立即返回，不等待LLM完成）",
)
async def evaluate_batch_async(
    request: Request,
    files: Annotated[
        list[UploadFile],
        File(description="PDF 或 DOCX 文献文件，最多5个"),
    ],
) -> AsyncBatchResponse:
    """Upload files and evaluate them in background. Returns immediately."""
    if len(files) > 5:
        raise HTTPException(status_code=400, detail="最多5个文件")
    if not files:
        raise HTTPException(status_code=400, detail="请至少上传一个文件")

    client_ip = request.client.host if request.client else "unknown"
    task_id = uuid.uuid4().hex[:12]
    file_data_list: list[tuple[bytes, str, str]] = []

    for f in files:
        content = await f.read()
        file_data_list.append((content, f.filename or "unknown", hashlib.md5(content).hexdigest()))

    def _process_one(content: bytes, filename: str, file_hash: str):
        """Process a single file — runs in a thread."""
        cached = lookup_cached_result(file_hash)
        if cached:
            logger.info("Async batch cache hit: %s", filename)
            return

        name, text = parse_document(BytesIO(content), filename)
        if not text.strip():
            return

        # Use async evaluator with parallel indicator calls (3 concurrent)
        import asyncio as _asyncio
        try:
            loop = _asyncio.new_event_loop()
            _asyncio.set_event_loop(loop)
            response = loop.run_until_complete(
                evaluate_document_async(
                    text=text, doc_name=name,
                    max_concurrency=5,  # 5 parallel LLM calls → ~3x faster
                )
            )
            loop.close()
        except Exception:
            response = evaluate_document(text=text, doc_name=name)

        save_to_history(
            record_id=response.id, timestamp=response.timestamp,
            doc_name=response.doc_name, base_score=response.base_score,
            total_score=response.total_score, scale_factor=response.scale_factor,
            excluded_indicators=response.excluded_indicators,
            primary_results=response.primary_results,
            additional_results=response.additional_results,
            overall_comment=response.overall_comment,
            ip_address=client_ip, filename=filename, file_hash=file_hash,
        )

    from concurrent.futures import ThreadPoolExecutor as _TPE
    # Process all files in parallel, each with parallel indicators
    executor = _TPE(max_workers=min(len(file_data_list), 3))
    for content, filename, file_hash in file_data_list:
        executor.submit(_process_one, content, filename, file_hash)
    executor.shutdown(wait=False)  # fire-and-forget

    logger.info("Async batch task %s: %d files running in parallel", task_id, len(file_data_list))

    return AsyncBatchResponse(
        task_id=task_id,
        file_count=len(file_data_list),
        filenames=[fn for _, fn, _ in file_data_list],
        message=f"已接收 {len(file_data_list)} 个文件，后台评价中。约 {len(file_data_list)*1.5}~{len(file_data_list)*3} 分钟后刷新页面查看结果",
    )


@router.post(
    "/report",
    summary="评价并下载报告",
    description="评价文献并以指定格式（docx/pdf/txt）下载评价报告。",
    responses={400: {"model": ErrorResponse}},
)
def evaluate_and_download_report(
    request: EvaluationRequest,
    req: Request,
    fmt: Annotated[str, Query(description="报告格式: docx, pdf, txt")] = "docx",
) -> Response:
    """Evaluate a document and return the report as a downloadable file."""
    from backend.utils.report_generator import generate_report, get_mime_type, get_file_extension

    if fmt not in ("docx", "pdf", "txt"):
        raise HTTPException(status_code=400, detail=f"不支持的格式 '{fmt}'，可选 docx/pdf/txt")

    if not request.text.strip():
        raise HTTPException(status_code=400, detail="文献内容不能为空")

    response = evaluate_document(
        text=request.text,
        doc_name=request.doc_name,
    )

    save_to_history(
        record_id=response.id,
        timestamp=response.timestamp,
        doc_name=response.doc_name,
        base_score=response.base_score,
        total_score=response.total_score,
        scale_factor=response.scale_factor,
        excluded_indicators=response.excluded_indicators,
        primary_results=response.primary_results,
        additional_results=response.additional_results,
        overall_comment=response.overall_comment,
        ip_address=req.client.host if req.client else "unknown",
        username="",
    )

    report_bytes = generate_report(response, fmt)
    base_name = response.doc_name.rsplit(".", 1)[0]

    from urllib.parse import quote
    filename = f"评价报告_{base_name}{get_file_extension(fmt)}"
    return Response(
        content=report_bytes,
        media_type=get_mime_type(fmt),
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )
