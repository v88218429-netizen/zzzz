FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/data
ENV HEALTH_CHECK_INTERVAL_MIN=30
ENV FINANCE_SYNC_INTERVAL_MIN=360
ENV FINANCE_DATE_FROM=2026-09-01

RUN pip install --no-cache-dir "wb-mcp-server==2.6.1"

WORKDIR /app
COPY finance_sync.py /app/finance_sync.py
COPY main.py /app/main.py

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
