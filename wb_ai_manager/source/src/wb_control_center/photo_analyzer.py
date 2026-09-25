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
        return bool((self.settings.openai_api_key or "").strip())

    def status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": "openai",
            "model": self.model,
            "missing": [] if self.enabled else ["OPENAI_API_KEY"],
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

        if not self.enabled:
            return PhotoAnalysisResult(
                status="waiting_key",
                verdict="ЖДЁТ VISION",
                confidence_pct=None,
                substitution="НЕ ОЦЕНЕНО",
                damage="НЕ ОЦЕНЕНО",
                damage_severity="НЕИЗВЕСТНО",
                damage_types=[],
                visible_evidence="Vision-контур готов, но на Railway не задан OPENAI_API_KEY.",
                human_review=True,
                model=self.model,
            ).as_dict()

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
