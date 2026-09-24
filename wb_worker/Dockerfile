FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/data
ENV HEALTH_CHECK_INTERVAL_MIN=30

RUN pip install --no-cache-dir "wb-mcp-server==2.6.1"

EXPOSE 8001

CMD ["uvicorn", "wb_mcp.app:fastapi_app", "--host", "0.0.0.0", "--port", "8001"]
