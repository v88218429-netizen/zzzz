FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8001 \
    DATA_DIR=/data \
    HEALTH_CHECK_INTERVAL_MIN=30
RUN pip install --no-cache-dir "wb-mcp-server==2.6.1"
EXPOSE 8001
VOLUME ["/data"]
CMD ["sh", "-c", "uvicorn wb_mcp.app:fastapi_app --host 0.0.0.0 --port ${PORT:-8001}"]
