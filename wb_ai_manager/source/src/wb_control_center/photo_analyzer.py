from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Settings


_ALLOWED_IMAGE_HOST_RE = re.compile(
    r"^https://static-basket-[a-z0-9-]+\.wb\.ru/",
    re.IGNORECASE,
)


@dataclass
class PhotoAnalysisResult:
    status: str
    verdict: str
    confidence_pct: int | None
    substitution: str
    damage: str
    damage_severity: str
    damage_types: list[str]
    visible_evidence: str
    human_review: bool
    received_guess: str = ""
    model: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "verdict": self.verdict,
            "confidence_pct": self.confidence_pct,
            "substitution": self.substitution,
            "damage": self.damage,
            "damage_severity": self.damage_severity,
            "damage_types": self.damage_types,
            "visible_evidence": self.visible_evidence,
            "human_review": self.human_review,
            "received_guess": self.received_guess,
            "model": self.model,
        }


class PhotoAnalyzer:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def model(self) -> str:
        return (getattr(self.settings, "vision_model", "") or "gpt-5.6-luna").strip()

    @property
    def enabled(self) -> bool:
        return True

    @property
    def provider(self) -> str:
        return "openai" if (self.settings.openai_api_key or "").strip() else "local"

    @property
    def local_model(self) -> str:
        return (getattr(self.settings, "local_vision_model", "") or "HuggingFaceTB/SmolVLM2-500M-Video-Instruct").strip()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "provider": self.provider,
            "model": self.model if self.provider == "openai" else self.local_model,
            "missing": [],
            "api_key_required": self.provider == "openai",
        }

    @staticmethod
    def _clean_urls(photo_urls: list[str]) -> list[str]:
        out: list[str] = []
        for raw in photo_urls or []:
            url = str(raw or "").strip()
            if not url or not _ALLOWED_IMAGE_HOST_RE.match(url):
                continue
            if url not in out:
                out.append(url)
            if len(out) >= 5:
                break
        return out

    async def _analyze_local(
        self,
        *,
        expected: str,
        reported_received: str,
        category: str,
        shop: str,
        sticker: str,
        nm_id: str,
        photo_urls: list[str],
    ) -> dict[str, Any]:
        import asyncio
        from io import BytesIO

        import torch
        from PIL import Image
        from transformers import AutoProcessor, AutoModelForImageTextToText

        if not hasattr(self, "_local_processor"):
            self._local_processor = None
            self._local_model_obj = None

        if self._local_processor is None:
            self._local_processor = await asyncio.to_thread(
                AutoProcessor.from_pretrained,
                self.local_model,
            )

        if self._local_model_obj is None:
            def _load_model():
                model = AutoModelForImageTextToText.from_pretrained(
                    self.local_model,
                    torch_dtype=torch.float16,
                    low_cpu_mem_usage=True,
                )
                model.eval()
                return model

            self._local_model_obj = await asyncio.to_thread(_load_model)

        images: list[Image.Image] = []
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            for url in photo_urls[:5]:
                try:
                    r = await client.get(url)
                    if r.status_code != 200:
                        continue
                    img = Image.open(BytesIO(r.content)).convert("RGB")
                    if img.width > 1400 or img.height > 1400:
                        img.thumbnail((1400, 1400))
                    images.append(img)
                except Exception:
                    continue

        if not images:
            return PhotoAnalysisResult(
                status="insufficient",
                verdict="НЕДОСТАТОЧНО ДАННЫХ",
                confidence_pct=0,
                substitution="ВОЗМОЖНО",
                damage="ВОЗМОЖНО",
                damage_severity="НЕИЗВЕСТНО",
                damage_types=[],
                visible_evidence="Не удалось загрузить WB-фото.",
                human_review=True,
                model=self.local_model,
            ).as_dict()

        prompt = f"""
Ты проверяешь претензию фулфилменту по фотографиям Wildberries.
Магазин: {shop or 'не указан'}
Категория кейса: {category or 'не указана'}
Sticker: {sticker or 'не указан'}
nmId: {nm_id or 'не указан'}
Должно было прийти: {expected or 'не указано'}
В выгрузке указано как пришедшее: {reported_received or 'не указано'}

Проанализируй ВСЕ изображения как единый набор доказательств.
Определи подмену/пересорт по типу, модели, цвету, объёму, количеству и комплектности.
Отдельно оцени повреждение: есть/нет, степень и характер.
Не угадывай невидимое.

Верни ТОЛЬКО JSON без markdown:
{{
 "status":"analyzed"|"insufficient",
 "verdict":"СООТВЕТСТВУЕТ"|"ПОДМЕНА"|"ПОВРЕЖДЕНИЕ"|"ПОДМЕНА И ПОВРЕЖДЕНИЕ"|"НЕДОСТАТОЧНО ДАННЫХ",
 "confidence_pct":0,
 "substitution":"ДА"|"НЕТ"|"ВОЗМОЖНО",
 "damage":"ЕСТЬ"|"НЕТ"|"ВОЗМОЖНО",
 "damage_severity":"НЕТ"|"ЛЁГКОЕ"|"СРЕДНЕЕ"|"СИЛЬНОЕ"|"КРИТИЧЕСКОЕ"|"НЕИЗВЕСТНО",
 "damage_types":[],
 "visible_evidence":"кратко, конкретно, по видимым признакам",
 "human_review":true,
 "received_guess":"что фактически видно"
}}
""".strip()

        content = [{"type": "image"} for _ in images]
        content.append({"type": "text", "text": prompt})
        messages = [{"role": "user", "content": content}]

        processor = self._local_processor
        model = self._local_model_obj

        def _run():
            chat = processor.apply_chat_template(
                messages,
                add_generation_prompt=True,
            )
            inputs = processor(
                text=chat,
                images=images,
                return_tensors="pt",
            )
            with torch.inference_mode():
                generated = model.generate(
                    **inputs,
                    max_new_tokens=420,
                    do_sample=False,
                )
            input_len = inputs["input_ids"].shape[1]
            return processor.batch_decode(
                generated[:, input_len:],
                skip_special_tokens=True,
            )[0]

        output_text = await asyncio.to_thread(_run)
        try:
            data = json.loads(output_text.strip())
        except Exception:
            match = re.search(r"\{.*\}", output_text, re.DOTALL)
            if not match:
                return PhotoAnalysisResult(
                    status="insufficient",
                    verdict="НЕДОСТАТОЧНО ДАННЫХ",
                    confidence_pct=0,
                    substitution="ВОЗМОЖНО",
                    damage="ВОЗМОЖНО",
                    damage_severity="НЕИЗВЕСТНО",
                    damage_types=[],
                    visible_evidence="Локальная модель не вернула структурированный результат: " + output_text[:280],
                    human_review=True,
                    model=self.local_model,
                ).as_dict()
            data = json.loads(match.group(0))

        data["model"] = self.local_model
        data["provider"] = "local"
        return data

    async def analyze(
        self,
        *,
        expected: str,
        reported_received: str,
        category: str,
        shop: str,
        sticker: str,
        nm_id: str,
        photo_urls: list[str],
    ) -> dict[str, Any]:
        urls = self._clean_urls(photo_urls)
        if not urls:
            return PhotoAnalysisResult(
                status="insufficient",
                verdict="НЕДОСТАТОЧНО ДАННЫХ",
                confidence_pct=None,
                substitution="ВОЗМОЖНО",
                damage="НЕ ОЦЕНЕНО",
                damage_severity="НЕИЗВЕСТНО",
                damage_types=[],
                visible_evidence="Нет доступных WB-фото для визуального анализа.",
                human_review=True,
                model=self.model,
            ).as_dict()

        if self.provider == "local":
            return await self._analyze_local(
                expected=expected,
                reported_received=reported_received,
                category=category,
                shop=shop,
                sticker=sticker,
                nm_id=nm_id,
                photo_urls=urls,
            )

        prompt = f"""
Ты проверяешь претензию фулфилменту по фотографиям Wildberries.

Контекст:
- магазин: {shop or 'не указан'}
- категория кейса: {category or 'не указана'}
- sticker: {sticker or 'не указан'}
- nmId: {nm_id or 'не указан'}
- должно было прийти: {expected or 'не указано'}
- в выгрузке указано как пришедшее: {reported_received or 'не указано'}

Проанализируй ВСЕ приложенные фотографии как единый набор доказательств.
Нужно отдельно определить:
1) соответствует ли фактический товар ожидаемому;
2) есть ли подмена/пересорт по типу, модели, цвету, объёму, количеству или комплектности;
3) есть ли видимое повреждение;
4) если повреждение есть — его степень и характер;
5) какие конкретно признаки видны на фото.

Не додумывай невидимое. Если объём/размер/количество нельзя надёжно установить, так и напиши.
Не считай повреждением обычные блики, плёнку, следы упаковки или перспективное искажение, если нет явных признаков.

Верни только JSON со строго такими полями:
{{
  "status": "analyzed" | "insufficient",
  "verdict": "СООТВЕТСТВУЕТ" | "ПОДМЕНА" | "ПОВРЕЖДЕНИЕ" | "ПОДМЕНА И ПОВРЕЖДЕНИЕ" | "НЕДОСТАТОЧНО ДАННЫХ",
  "confidence_pct": integer 0..100,
  "substitution": "ДА" | "НЕТ" | "ВОЗМОЖНО",
  "damage": "ЕСТЬ" | "НЕТ" | "ВОЗМОЖНО",
  "damage_severity": "НЕТ" | "ЛЁГКОЕ" | "СРЕДНЕЕ" | "СИЛЬНОЕ" | "КРИТИЧЕСКОЕ" | "НЕИЗВЕСТНО",
  "damage_types": ["трещина", "скол", "вмятина", "деформация", "царапины", "сломанная деталь", "разрыв упаковки", "загрязнение", "следы использования", "некомплект", "другое"],
  "visible_evidence": "краткое конкретное объяснение на русском",
  "human_review": true | false,
  "received_guess": "что фактически видно/опознано, если можно определить"
}}
""".strip()

        content: list[dict[str, Any]] = [{"type": "input_text", "text": prompt}]
        for url in urls:
            content.append({"type": "input_image", "image_url": url, "detail": "high"})

        payload: dict[str, Any] = {
            "model": self.model,
            "input": [{"role": "user", "content": content}],
            "max_output_tokens": 1400,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "ff_photo_analysis",
                    "strict": True,
                    "schema": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "status": {"type": "string", "enum": ["analyzed", "insufficient"]},
                            "verdict": {"type": "string", "enum": [
                                "СООТВЕТСТВУЕТ",
                                "ПОДМЕНА",
                                "ПОВРЕЖДЕНИЕ",
                                "ПОДМЕНА И ПОВРЕЖДЕНИЕ",
                                "НЕДОСТАТОЧНО ДАННЫХ"
                            ]},
                            "confidence_pct": {"type": "integer", "minimum": 0, "maximum": 100},
                            "substitution": {"type": "string", "enum": ["ДА", "НЕТ", "ВОЗМОЖНО"]},
                            "damage": {"type": "string", "enum": ["ЕСТЬ", "НЕТ", "ВОЗМОЖНО"]},
                            "damage_severity": {"type": "string", "enum": [
                                "НЕТ", "ЛЁГКОЕ", "СРЕДНЕЕ", "СИЛЬНОЕ", "КРИТИЧЕСКОЕ", "НЕИЗВЕСТНО"
                            ]},
                            "damage_types": {
                                "type": "array",
                                "items": {"type": "string"},
                                "maxItems": 8
                            },
                            "visible_evidence": {"type": "string"},
                            "human_review": {"type": "boolean"},
                            "received_guess": {"type": "string"}
                        },
                        "required": [
                            "status","verdict","confidence_pct","substitution","damage",
                            "damage_severity","damage_types","visible_evidence",
                            "human_review","received_guess"
                        ]
                    }
                }
            }
        }

        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(
                "https://api.openai.com/v1/responses",
                headers=headers,
                json=payload,
            )
            if response.status_code >= 400:
                # Fallback for API/schema changes: request plain JSON text.
                fallback = dict(payload)
                fallback.pop("text", None)
                response = await client.post(
                    "https://api.openai.com/v1/responses",
                    headers=headers,
                    json=fallback,
                )
            response.raise_for_status()
            raw = response.json()

        text_parts: list[str] = []
        for item in raw.get("output") or []:
            for part in item.get("content") or []:
                value = part.get("text")
                if value:
                    text_parts.append(str(value))
        output_text = "\n".join(text_parts).strip()
        if not output_text and raw.get("output_text"):
            output_text = str(raw.get("output_text")).strip()

        try:
            data = json.loads(output_text)
        except Exception:
            match = re.search(r"\{.*\}", output_text, re.DOTALL)
            if not match:
                raise RuntimeError("Vision model returned non-JSON output")
            data = json.loads(match.group(0))

        data["model"] = self.model
        return data
