"""Rate limiting middleware using a sliding-window token-bucket algorithm.

Designed for the single-GPU inference scenario where concurrent
evaluation requests would contend for VRAM and degrade latency.
"""

import time
import asyncio
import logging
from collections import defaultdict
from typing import Callable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.models.schemas import ErrorResponse

logger = logging.getLogger(__name__)


# ── Sliding-window rate limiter ────────────────────────────────────
class SlidingWindowLimiter:
    """Track request timestamps per client in a sliding window."""

    def __init__(self, max_requests: int = 5, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._clients: dict[str, list[float]] = defaultdict(list)

    def _prune(self, client_id: str, now: float) -> None:
        """Remove timestamps outside the current window."""
        cutoff = now - self.window_seconds
        self._clients[client_id] = [
            ts for ts in self._clients[client_id] if ts > cutoff
        ]

    def allow(self, client_id: str) -> bool:
        """Check if a request is allowed. Returns True if within limits."""
        now = time.time()
        self._prune(client_id, now)
        if len(self._clients[client_id]) >= self.max_requests:
            return False
        self._clients[client_id].append(now)
        return True

    def remaining(self, client_id: str) -> int:
        """Return remaining requests in current window."""
        now = time.time()
        self._prune(client_id, now)
        return max(0, self.max_requests - len(self._clients[client_id]))


# Default limiter: 10 evaluation requests per minute per client
_evaluation_limiter = SlidingWindowLimiter(max_requests=10, window_seconds=60)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Apply rate limiting to evaluation endpoints.

    Identifies clients by X-Forwarded-For header or remote IP.
    """

    def __init__(self, app, limiter: SlidingWindowLimiter | None = None):
        super().__init__(app)
        self.limiter = limiter or _evaluation_limiter

    async def dispatch(self, request: Request, call_next: Callable):
        # Only rate-limit evaluation endpoints
        if request.url.path.startswith("/evaluate"):
            client_id = self._get_client_id(request)
            if not self.limiter.allow(client_id):
                remaining = self.limiter.remaining(client_id)
                retry_after = self.limiter.window_seconds
                logger.warning("Rate limit exceeded for client: %s", client_id)
                return JSONResponse(
                    status_code=429,
                    content=ErrorResponse(
                        error="rate_limit_exceeded",
                        message=(
                            f"请求频率过高，请在 {retry_after} 秒后重试。"
                            f"每 {self.limiter.window_seconds} 秒最多 "
                            f"{self.limiter.max_requests} 次请求。"
                        ),
                    ).model_dump(),
                    headers={"Retry-After": str(retry_after)},
                )

        return await call_next(request)

    @staticmethod
    def _get_client_id(request: Request) -> str:
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"
