from __future__ import annotations

import json
import os
import re
from io import BytesIO
from typing import Any

import httpx
import torch
from fastapi import FastAPI, Header, HTTPException
from PIL import Image
from transformers import AutoProcessor, AutoModelForImageTextToText

MODEL_ID = os.getenv("MODEL_ID", "HuggingFaceTB/SmolVLM2-500M-Video-Instruct")
ACCESS_KEY = os.getenv("ACCESS_KEY", "")
MAX_IMAGES = int(os.getenv("MAX_IMAGES", "5"))

app = FastAPI(title="FF Vision Local", version="1.0.0")
_processor = None
_model = None

ALLOWED = re.compile(r"^https://static-basket-[a-z0-9-]+\.wb\.ru/", re.I)


def load_model():
    global _processor, _model
    if _processor is None:
        _processor = AutoProcessor.from_pretrained(MODEL_ID)
    if _model is None:
        _model = AutoModelForImageTextToText.from_pretrained(
            MODEL_ID,
            torch_dtype=torch.float32,
            low_cpu_mem_usage=True,
        )
        _model.eval()
    return _processor, _model


@app.get("/health")
def health():
    return {
        "status": "ok",
        "provider": "local",
        "model": MODEL_ID,
        "loaded": _model is not None,
        "api_key_required": False,
    }


async def fetch_images(urls: list[str]) -> list[Image.Image]:
    out: list[Image.Image] = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for raw in urls[:MAX_IMAGES]:
            url = str(raw or "").strip()
            if not ALLOWED.match(url):
                continue
            r = await client.get(url)
            if r.status_code != 200:
                continue
            try:
                img = Image.open(BytesIO(r.content)).convert("RGB")
                if img.width > 1600 or img.height > 1600:
                    img.thumbnail((1600, 1600))
                out.append(img)
            except Exception:
                continue
    return out


def prompt_for(payload: dict[str, Any]) -> str:
    return f"""
Ты анализируешь фото претензии по товару Wildberries.
Магазин: {payload.get('shop') or 'не указан'}
Категория: {payload.get('category') or 'не указана'}
Ожидалось: {payload.get('expected') or 'не указано'}
В выгрузке указано как пришедшее: {payload.get('reported_received') or 'не указано'}

Посмотри ВСЕ изображения как единый набор доказательств.
Определи:
- подмена/пересорт: тип, модель, цвет, объём, количество, комплектность;
- повреждение: есть/нет;
- степень: НЕТ, ЛЁГКОЕ, СРЕДНЕЕ, СИЛЬНОЕ, КРИТИЧЕСКОЕ, НЕИЗВЕСТНО;
- характер: трещина, скол, вмятина, деформация, царапины, сломанная деталь, разрыв упаковки, загрязнение, следы использования, некомплект, другое;
- конкретные видимые признаки.

Не угадывай то, чего не видно.
Верни ТОЛЬКО JSON без markdown:
{{
 "status":"analyzed"|"insufficient",
 "verdict":"СООТВЕТСТВУЕТ"|"ПОДМЕНА"|"ПОВРЕЖДЕНИЕ"|"ПОДМЕНА И ПОВРЕЖДЕНИЕ"|"НЕДОСТАТОЧНО ДАННЫХ",
 "confidence_pct":0,
 "substitution":"ДА"|"НЕТ"|"ВОЗМОЖНО",
 "damage":"ЕСТЬ"|"НЕТ"|"ВОЗМОЖНО",
 "damage_severity":"НЕТ"|"ЛЁГКОЕ"|"СРЕДНЕЕ"|"СИЛЬНОЕ"|"КРИТИЧЕСКОЕ"|"НЕИЗВЕСТНО",
 "damage_types":[],
 "visible_evidence":"...",
 "human_review":true,
 "received_guess":"..."
}}
""".strip()


def parse_json(text: str) -> dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError("model returned non-JSON")
        return json.loads(m.group(0))


@app.post("/analyze")
async def analyze(payload: dict[str, Any], x_bridge_key: str | None = Header(default=None)):
    if ACCESS_KEY and x_bridge_key != ACCESS_KEY:
        raise HTTPException(status_code=401, detail="unauthorized")

    urls = payload.get("photo_urls") or []
    if not isinstance(urls, list):
        raise HTTPException(status_code=400, detail="photo_urls must be array")

    images = await fetch_images(urls)
    if not images:
        return {
            "status":"insufficient",
            "verdict":"НЕДОСТАТОЧНО ДАННЫХ",
            "confidence_pct":0,
            "substitution":"ВОЗМОЖНО",
            "damage":"ВОЗМОЖНО",
            "damage_severity":"НЕИЗВЕСТНО",
            "damage_types":[],
            "visible_evidence":"Не удалось загрузить подходящие WB-фото.",
            "human_review":True,
            "received_guess":"",
            "model":MODEL_ID,
            "provider":"local",
        }

    processor, model = load_model()
    content = [{"type":"image"} for _ in images]
    content.append({"type":"text","text":prompt_for(payload)})
    messages = [{"role":"user","content":content}]

    prompt = processor.apply_chat_template(messages, add_generation_prompt=True)
    inputs = processor(
        text=prompt,
        images=images,
        return_tensors="pt",
    )

    with torch.inference_mode():
        generated = model.generate(
            **inputs,
            max_new_tokens=500,
            do_sample=False,
        )

    input_len = inputs["input_ids"].shape[1]
    text_out = processor.batch_decode(
        generated[:, input_len:],
        skip_special_tokens=True,
    )[0]

    try:
        result = parse_json(text_out)
    except Exception:
        result = {
            "status":"insufficient",
            "verdict":"НЕДОСТАТОЧНО ДАННЫХ",
            "confidence_pct":0,
            "substitution":"ВОЗМОЖНО",
            "damage":"ВОЗМОЖНО",
            "damage_severity":"НЕИЗВЕСТНО",
            "damage_types":[],
            "visible_evidence":"Локальная vision-модель не вернула структурированный JSON: " + text_out[:300],
            "human_review":True,
            "received_guess":"",
        }

    result["model"] = MODEL_ID
    result["provider"] = "local"
    return result
