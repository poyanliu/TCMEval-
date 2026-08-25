# ── TCM Literature Evaluation System ──────────────────────────────
# Multi-service Docker image (API + Streamlit).
# Inference uses ZhipuAI cloud API — no GPU required.
#
# Build:
#   docker build -t tcm-evaluation .
#
# Run API server:
#   docker run -p 8000:8000 tcm-evaluation api
#
# Run Streamlit frontend:
#   docker run -p 6006:6006 tcm-evaluation streamlit
#

FROM python:3.10-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

# ── System dependencies ────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr tesseract-ocr-chi-sim poppler-utils curl \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ────────────────────────────────────────────
COPY requirements.txt /app/requirements.txt
WORKDIR /app
RUN pip install --no-cache-dir -r requirements.txt \
    -i https://pypi.tuna.tsinghua.edu.cn/simple

# ── Application code ───────────────────────────────────────────────
COPY . /app/

# ── Data persistence ───────────────────────────────────────────────
RUN mkdir -p /app/data
VOLUME ["/app/data"]

# ── Environment ────────────────────────────────────────────────────
ENV TCM_API_HOST=0.0.0.0 \
    TCM_API_PORT=8000

EXPOSE 8000 6006

COPY entrypoint.sh /app/entrypoint.sh
RUN chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["api"]
