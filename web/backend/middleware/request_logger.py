"""Structured request logging middleware.

Logs every API request with duration, status code, and client info.
Integrates with the evaluation service to tag long-running requests.
"""

import time
import logging
import uuid
from typing import Callable

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("tcm_api.access")


# ── Request ID injection ───────────────────────────────────────────
class RequestIDMiddleware(BaseHTTPMiddleware):
    """Assign a unique ID to every request for traceability."""

    async def dispatch(self, request: Request, call_next: Callable):
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())[:12]
        request.state.request_id = request_id
        response: Response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


# ── Access logging ─────────────────────────────────────────────────
class AccessLogMiddleware(BaseHTTPMiddleware):
    """Log request method, path, status, and duration in structured format.

    Skips health-check and static-asset paths to reduce noise.
    """

    SKIP_PREFIXES: tuple[str, ...] = (
        "/health", "/_stcore", "/static",
    )

    async def dispatch(self, request: Request, call_next: Callable):
        path = request.url.path

        # Skip noise
        if any(path.startswith(p) for p in self.SKIP_PREFIXES):
            return await call_next(request)

        start = time.monotonic()
        response: Response = await call_next(request)
        elapsed_ms = (time.monotonic() - start) * 1000

        client_ip = (
            request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or (request.client.host if request.client else "-")
        )
        request_id = getattr(request.state, "request_id", "-")

        logger.info(
            "method=%s path=%s status=%s duration_ms=%.0f client=%s req_id=%s",
            request.method, path, response.status_code, elapsed_ms,
            client_ip, request_id,
        )

        return response


# ── Slow request warning ───────────────────────────────────────────
SLOW_THRESHOLD_MS: float = 30_000  # 30 seconds


class SlowRequestMiddleware(BaseHTTPMiddleware):
    """Warn when a request exceeds the slow threshold.

    Useful for detecting stuck inference calls or OOM scenarios.
    """

    async def dispatch(self, request: Request, call_next: Callable):
        start = time.monotonic()
        response = await call_next(request)
        elapsed_ms = (time.monotonic() - start) * 1000

        if elapsed_ms > SLOW_THRESHOLD_MS:
            logger.warning(
                "SLOW_REQUEST path=%s duration_ms=%.0f",
                request.url.path, elapsed_ms,
            )

        return response
