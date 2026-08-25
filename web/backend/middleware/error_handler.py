"""Global exception handlers and optional authentication middleware."""

import logging
import traceback
from typing import Callable

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from backend.models.schemas import ErrorResponse

logger = logging.getLogger(__name__)


# ── Exception handlers ─────────────────────────────────────────────
def register_exception_handlers(app: FastAPI) -> None:
    """Attach global exception handlers to a FastAPI application."""

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        return JSONResponse(
            status_code=exc.status_code,
            content=ErrorResponse(
                error="http_error",
                message=exc.detail,
            ).model_dump(),
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError):
        logger.warning("ValueError: %s", exc)
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error="validation_error",
                message=str(exc),
            ).model_dump(),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error="internal_error",
                message="服务器内部错误，请稍后重试",
                detail=str(exc) if app.debug else None,
            ).model_dump(),
        )


# ── Optional auth middleware (stub) ────────────────────────────────
class AuthMiddleware(BaseHTTPMiddleware):
    """Placeholder authentication middleware.

    When enabled, checks for an X-API-Token header. Extend this class
    to integrate with the parent application's auth system.
    """

    def __init__(self, app, required: bool = False):
        super().__init__(app)
        self.required = required

    async def dispatch(self, request: Request, call_next: Callable):
        if self.required:
            token = request.headers.get("X-API-Token")
            if not token:
                return JSONResponse(
                    status_code=401,
                    content=ErrorResponse(
                        error="unauthorized",
                        message="缺少认证令牌",
                    ).model_dump(),
                )
            # TODO: Validate token against parent app's auth system
            if not self._validate_token(token):
                return JSONResponse(
                    status_code=403,
                    content=ErrorResponse(
                        error="forbidden",
                        message="认证令牌无效或已过期",
                    ).model_dump(),
                )

        return await call_next(request)

    @staticmethod
    def _validate_token(token: str) -> bool:
        """Stub — replace with real token validation logic."""
        return bool(token)
