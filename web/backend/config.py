"""Application configuration — server settings, LLM API, generation defaults."""

import os

# ── Load .env file (if present) ─────────────────────────────────────
_ENV_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".env",
)
if os.path.exists(_ENV_FILE):
    with open(_ENV_FILE, "r", encoding="utf-8") as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _key, _, _val = _line.partition("=")
                _key, _val = _key.strip(), _val.strip().strip("\"'")
                if _key and _val and _key not in os.environ:
                    os.environ[_key] = _val

# ── Paths ──────────────────────────────────────────────────────────
BASE_DIR: str = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR: str = os.environ.get(
    "TCM_DATA_DIR",
    os.path.join(os.path.dirname(__file__), "..", "data"),
)

HISTORY_FILE: str = os.environ.get(
    "TCM_HISTORY_FILE",
    os.path.join(DATA_DIR, "eval_history.json"),
)

DATABASE_PATH: str = os.environ.get(
    "TCM_DATABASE_PATH",
    os.path.join(DATA_DIR, "evaluations.db"),
)

# ── Server ─────────────────────────────────────────────────────────
API_HOST: str = os.environ.get("TCM_API_HOST", "0.0.0.0")
API_PORT: int = int(os.environ.get("TCM_API_PORT", "8000"))
API_WORKERS: int = int(os.environ.get("TCM_API_WORKERS", "1"))

# ── LLM API (ZhipuAI / OpenAI-compatible) ───────────────────────
ZHIPUAI_API_KEY: str = os.environ.get("ZHIPUAI_API_KEY", "")
ZHIPUAI_BASE_URL: str = os.environ.get(
    "ZHIPUAI_BASE_URL",
    "https://open.bigmodel.cn/api/paas/v4/",
)
ZHIPUAI_MODEL: str = os.environ.get("ZHIPUAI_MODEL", "glm-4-flash")
# Fallback models if primary is unavailable
ZHIPUAI_FALLBACK_MODELS: list[str] = [
    "glm-4-flash",
    "glm-4-air",
    "glm-4",
]

# ── Generation parameters ────────────────────────────────────────
MAX_NEW_TOKENS: int = 512
TEMPERATURE: float = 0.3
TOP_P: float = 0.9
REPETITION_PENALTY: float = 1.1

# ── Qwen / DashScope Vision API (for image understanding) ────────
DASHSCOPE_API_KEY: str = os.environ.get("DASHSCOPE_API_KEY", "")
DASHSCOPE_BASE_URL: str = os.environ.get(
    "DASHSCOPE_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)
QWEN_VL_MODEL: str = os.environ.get("QWEN_VL_MODEL", "qwen-vl-plus")

# ── Image extraction limits ────────────────────────────────────────
MAX_EXTRACTED_IMAGES: int = 10
MAX_IMAGE_DIMENSION: int = 1024

# ── Document limits ────────────────────────────────────────────────
MAX_DOC_CHARS: int = 6000
MAX_FILE_SIZE_MB: int = 50

# ── OCR settings ────────────────────────────────────────────────────
OCR_DPI: int = int(os.environ.get("TCM_OCR_DPI", "200"))
OCR_LANG: str = os.environ.get("TCM_OCR_LANG", "chi_sim")
OCR_WORKERS: int = int(os.environ.get("TCM_OCR_WORKERS", "2"))
OCR_PREPROCESS: bool = os.environ.get("TCM_OCR_PREPROCESS", "1") == "1"
OCR_PSM: str = os.environ.get("TCM_OCR_PSM", "6")  # PSM 6: uniform block of text
OCR_MAX_IMAGE_PX: int = int(os.environ.get("TCM_OCR_MAX_IMAGE_PX", "3000"))
