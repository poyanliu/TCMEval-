#!/bin/bash
# ── TCM Evaluation System entrypoint ───────────────────────────────
# Usage: entrypoint.sh [api|streamlit|both]

set -e

MODE="${1:-api}"

cd /app

case "$MODE" in
    api)
        echo "[entrypoint] Starting FastAPI server on ${TCM_API_HOST}:${TCM_API_PORT}"
        exec python -m uvicorn backend.main:app \
            --host "${TCM_API_HOST}" \
            --port "${TCM_API_PORT}" \
            --workers 1 \
            --timeout-keep-alive 300
        ;;

    streamlit)
        STREAMLIT_PORT="${STREAMLIT_PORT:-6006}"
        STREAMLIT_BASE_PATH="${STREAMLIT_BASE_PATH:-}"
        echo "[entrypoint] Starting Streamlit on 0.0.0.0:${STREAMLIT_PORT}"

        ARGS=(
            run /app/streamlit_app.py
            --server.port "${STREAMLIT_PORT}"
            --server.address 0.0.0.0
            --server.enableCORS false
            --server.enableXsrfProtection false
        )
        if [ -n "${STREAMLIT_BASE_PATH}" ]; then
            ARGS+=(--server.baseUrlPath "${STREAMLIT_BASE_PATH}")
        fi

        exec streamlit "${ARGS[@]}"
        ;;

    both)
        echo "[entrypoint] Starting both API and Streamlit..."
        # Start API in background
        python -m uvicorn backend.main:app \
            --host "${TCM_API_HOST}" \
            --port "${TCM_API_PORT}" \
            --workers 1 &
        API_PID=$!

        # Start Streamlit in foreground
        STREAMLIT_PORT="${STREAMLIT_PORT:-6006}"
        streamlit run /app/streamlit_app.py \
            --server.port "${STREAMLIT_PORT}" \
            --server.address 0.0.0.0 \
            --server.enableCORS false \
            --server.enableXsrfProtection false &
        ST_PID=$!

        # Wait for either to exit
        trap "kill ${API_PID} ${ST_PID} 2>/dev/null" EXIT
        wait -n
        ;;

    *)
        echo "Usage: $0 {api|streamlit|both}"
        exit 1
        ;;
esac
