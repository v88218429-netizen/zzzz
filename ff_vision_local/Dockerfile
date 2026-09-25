FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/opt/hf-cache \
    TRANSFORMERS_CACHE=/opt/hf-cache

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg62-turbo libpng16-16 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ARG MODEL_ID=HuggingFaceTB/SmolVLM2-500M-Video-Instruct
ENV MODEL_ID=$MODEL_ID

RUN python - <<'PY'
from transformers import AutoProcessor, AutoModelForImageTextToText
import os
m=os.environ["MODEL_ID"]
AutoProcessor.from_pretrained(m)
AutoModelForImageTextToText.from_pretrained(m, low_cpu_mem_usage=True)
print("cached", m)
PY

COPY app.py .

CMD ["sh","-lc","uvicorn app:app --host 0.0.0.0 --port ${PORT:-8080}"]
