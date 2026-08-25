"""FastAPI application entry point for the TCM Literature Evaluation System.

Start with:
    uvicorn backend.main:app --host 0.0.0.0 --port 8000

Or for development with hot reload:
    uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
"""

import sys
import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Ensure project root is on the Python path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from backend.config import API_HOST, API_PORT
from backend.routers.evaluation import router as evaluation_router
from backend.routers.history import router as history_router
from backend.routers.auth import router as auth_router
from backend.middleware.error_handler import (
    register_exception_handlers,
    AuthMiddleware,
)
from backend.services.llm_client import load_model

# ── Logging ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("tcm_api")


# ── Application lifecycle ──────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize API client. Shutdown: cleanup resources."""
    logger.info("Starting TCM Evaluation API on %s:%s", API_HOST, API_PORT)
    try:
        load_model()
        logger.info("API client initialized")
    except Exception as exc:
        logger.warning("API client init failed (will retry on first request): %s", exc)

    yield

    logger.info("Shutting down...")
    logger.info("Shutdown complete")


# ── App instance ───────────────────────────────────────────────────
app = FastAPI(
    title="中医药政策文献智能评价系统 API",
    description=(
        "基于智谱 GLM-4 云端大模型的中医药政策研究文献多维度自动评分系统。"
        "支持 PDF/DOCX 文献上传、批量评价、历史记录管理。"
    ),
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ── CORS ───────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Exception handling ─────────────────────────────────────────────
register_exception_handlers(app)

# ── Routers ────────────────────────────────────────────────────────
app.include_router(evaluation_router)
app.include_router(history_router)
app.include_router(auth_router)

# ── Static file serving ────────────────────────────────────────────
from fastapi.responses import FileResponse, HTMLResponse


@app.get("/", response_class=HTMLResponse)
async def index():
    """Serve the main HTML single-page frontend."""
    frontend_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "frontend", "index.html",
    )
    if os.path.exists(frontend_path):
        with open(frontend_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>前端页面未找到</h1>", status_code=404)

@app.get("/api/upload", response_class=HTMLResponse)
async def upload_page():
    """Serve the direct HTTP upload page."""
    assets_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "assets",
    )
    path = os.path.join(assets_dir, "upload.html")
    if os.path.exists(path):
        return HTMLResponse(content=open(path, "r").read())
    return HTMLResponse(content="<h1>Upload page not found</h1>", status_code=404)


# ── Health check ───────────────────────────────────────────────────
@app.get("/health", tags=["system"])
def health_check():
    """Return system health status."""
    return {
        "status": "healthy",
    }


# ── Excel download ──────────────────────────────────────────────────
@app.get("/api/download/survey-data", tags=["download"])
def download_survey_excel():
    """Download survey/questionnaire Excel data."""
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "data", "batch_evaluation_summary.xlsx",
    )
    if not os.path.exists(path):
        from fastapi.responses import JSONResponse
        return JSONResponse({"detail": "File not found"}, status_code=404)
    return FileResponse(
        path,
        filename="batch_evaluation_summary.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ── Direct runner ──────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=API_HOST,
        port=API_PORT,
        reload=False,
    )
