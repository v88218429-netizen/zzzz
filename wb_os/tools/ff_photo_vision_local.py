#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import requests


def extract_clasp_return(text: str) -> Any:
    raw = text.strip()
    candidates: list[str] = []

    # clasp commonly prints: Returned value: "..."
    m = re.search(r"Returned value:\s*(.+)$", raw, re.MULTILINE | re.DOTALL)
    if m:
        candidates.append(m.group(1).strip())

    # Also try each non-empty line from the bottom.
    candidates.extend(
        line.strip()
        for line in reversed(raw.splitlines())
        if line.strip()
    )
    candidates.append(raw)

    for candidate in candidates:
        value = candidate
        for _ in range(3):
            try:
                decoded = json.loads(value)
            except Exception:
                break
            if isinstance(decoded, str):
                value = decoded.strip()
                continue
            return decoded

        # Last resort: locate a JSON array/object inside the line.
        for opener, closer in (("[", "]"), ("{", "}")):
            left = value.find(opener)
            right = value.rfind(closer)
            if left >= 0 and right > left:
                snippet = value[left : right + 1]
                try:
                    decoded = json.loads(snippet)
                    if isinstance(decoded, str):
                        decoded = json.loads(decoded)
                    return decoded
                except Exception:
                    pass

    raise RuntimeError("Could not parse clasp return value")


def _download_bytes(url: str, timeout: int = 40) -> bytes:
    r = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": "ff-photo-vision/1.0"},
    )
    r.raise_for_status()
    return r.content


def expand_photo_source(url: str, timeout: int = 40) -> list[str]:
    """Return direct image URLs for WB images or a public Yandex Disk link."""
    if re.match(r"^https://static-basket-[a-z0-9-]+\\.wb\\.ru/", url, re.I):
        return [url]

    if re.match(r"^https://disk\\.yandex\\.(ru|com)/", url, re.I):
        meta = requests.get(
            "https://cloud-api.yandex.net/v1/disk/public/resources",
            params={"public_key": url, "limit": 50},
            timeout=timeout,
        )
        meta.raise_for_status()
        data = meta.json() or {}

        direct: list[str] = []

        if data.get("type") == "file":
            mime = str(data.get("mime_type") or "")
            file_url = str(data.get("file") or "")
            if mime.startswith("image/") and file_url:
                direct.append(file_url)

        embedded = (data.get("_embedded") or {}).get("items") or []
        for item in embedded:
            if len(direct) >= 5:
                break
            if str(item.get("type") or "") != "file":
                continue
            mime = str(item.get("mime_type") or "")
            if not mime.startswith("image/"):
                continue

            file_url = str(item.get("file") or "")
            if file_url:
                direct.append(file_url)
                continue

            path = str(item.get("path") or "")
            if path:
                dl = requests.get(
                    "https://cloud-api.yandex.net/v1/disk/public/resources/download",
                    params={"public_key": url, "path": path},
                    timeout=timeout,
                )
                dl.raise_for_status()
                href = str((dl.json() or {}).get("href") or "")
                if href:
                    direct.append(href)

        return direct[:5]

    return []


def download_image_b64(url: str, timeout: int = 40) -> str:
    return base64.b64encode(_download_bytes(url, timeout=timeout)).decode("ascii")


def normalize_enum(value: Any, allowed: list[str], default: str) -> str:
    s = str(value or "").strip().upper()
    for x in allowed:
        if s == x.upper():
            return x
    return default


def normalize_result(data: dict[str, Any]) -> dict[str, Any]:
    verdict = normalize_enum(
        data.get("verdict"),
        [
            "СООТВЕТСТВУЕТ",
            "ПОДМЕНА",
            "ПОВРЕЖДЕНИЕ",
            "ПОДМЕНА И ПОВРЕЖДЕНИЕ",
            "НЕДОСТАТОЧНО ДАННЫХ",
        ],
        "НЕДОСТАТОЧНО ДАННЫХ",
    )
    substitution = normalize_enum(
        data.get("substitution"),
        ["ДА", "НЕТ", "ВОЗМОЖНО"],
        "ВОЗМОЖНО",
    )
    damage = normalize_enum(
        data.get("damage"),
        ["ЕСТЬ", "НЕТ", "ВОЗМОЖНО"],
        "ВОЗМОЖНО",
    )
    severity = normalize_enum(
        data.get("damage_severity"),
        ["НЕТ", "ЛЁГКОЕ", "СРЕДНЕЕ", "СИЛЬНОЕ", "КРИТИЧЕСКОЕ", "НЕИЗВЕСТНО"],
        "НЕИЗВЕСТНО",
    )

    try:
        confidence = int(round(float(data.get("confidence_pct", 0))))
    except Exception:
        confidence = 0
    confidence = max(0, min(100, confidence))

    damage_types = data.get("damage_types") or []
    if not isinstance(damage_types, list):
        damage_types = [str(damage_types)]
    damage_types = [str(x).strip() for x in damage_types if str(x).strip()][:8]

    human_review = data.get("human_review")
    if not isinstance(human_review, bool):
        human_review = confidence < 90 or verdict == "НЕДОСТАТОЧНО ДАННЫХ"

    return {
        "verdict": verdict,
        "confidence_pct": confidence,
        "substitution": substitution,
        "damage": damage,
        "damage_severity": severity,
        "damage_types": damage_types,
        "visible_evidence": str(data.get("visible_evidence") or "").strip()[:1500],
        "human_review": human_review,
        "received_guess": str(data.get("received_guess") or "").strip()[:500],
    }


def analyze_case(case: dict[str, Any], *, ollama_url: str, model: str) -> dict[str, Any]:
    photo_urls = [str(x) for x in (case.get("photo_urls") or []) if x]
    images: list[str] = []
    failed: list[str] = []

    for source_url in photo_urls[:5]:
        try:
            direct_urls = expand_photo_source(source_url)
            if not direct_urls:
                failed.append(f"{source_url}: unsupported or empty photo source")
                continue

            for direct_url in direct_urls:
                if len(images) >= 5:
                    break
                try:
                    images.append(download_image_b64(direct_url))
                except Exception as exc:
                    failed.append(f"{direct_url}: {exc}")
        except Exception as exc:
            failed.append(f"{source_url}: {exc}")

        if len(images) >= 5:
            break

    if not images:
        result = {
            "verdict": "НЕДОСТАТОЧНО ДАННЫХ",
            "confidence_pct": 0,
            "substitution": "ВОЗМОЖНО",
            "damage": "ВОЗМОЖНО",
            "damage_severity": "НЕИЗВЕСТНО",
            "damage_types": [],
            "visible_evidence": "Не удалось загрузить фотографии для анализа.",
            "human_review": True,
            "received_guess": "",
        }
        if failed:
            result["visible_evidence"] += " Ошибки загрузки: " + "; ".join(failed)[:700]
        return result

    prompt = f"""
Ты инспектор претензий фулфилменту. Перед тобой все фотографии одного кейса Wildberries.

Контекст:
магазин: {case.get('shop') or 'не указан'}
категория: {case.get('category') or 'не указана'}
sticker: {case.get('sticker') or 'не указан'}
nmId: {case.get('nm_id') or 'не указан'}
ДОЛЖНО БЫЛО ПРИЙТИ: {case.get('expected') or 'не указано'}
В ВЫГРУЗКЕ УКАЗАНО КАК ПРИШЕДШЕЕ: {case.get('reported_received') or 'не указано'}

Проверь ВСЕ фотографии вместе.

Определи отдельно:
1. Тот ли это товар: тип, модель/форма, цвет, объём, количество, комплектность.
2. Есть ли подмена или пересорт.
3. Есть ли физическое повреждение именно товара.
4. Если повреждение есть: степень и конкретный характер.
5. Какие признаки реально видны на фото.

Правила:
- Не угадывай то, чего не видно.
- Если объём, количество или размер нельзя надёжно определить по фото — так и напиши.
- Не считай бликом, плёнкой или перспективой повреждение без явных признаков.
- Повреждение оценивай как:
  ЛЁГКОЕ — косметическое, функциональность сохранена;
  СРЕДНЕЕ — заметное, но товар может использоваться;
  СИЛЬНОЕ — существенно деформирован/сломана часть;
  КРИТИЧЕСКОЕ — товар фактически непригоден.
- Если данные и фото противоречат друг другу, приоритет у того, что реально видно, но укажи противоречие.
- Для подмены не достаточно того, что написано в поле "что пришло": подтверди по фото, если возможно.

Верни ТОЛЬКО JSON:
{{
  "verdict": "СООТВЕТСТВУЕТ" | "ПОДМЕНА" | "ПОВРЕЖДЕНИЕ" | "ПОДМЕНА И ПОВРЕЖДЕНИЕ" | "НЕДОСТАТОЧНО ДАННЫХ",
  "confidence_pct": 0,
  "substitution": "ДА" | "НЕТ" | "ВОЗМОЖНО",
  "damage": "ЕСТЬ" | "НЕТ" | "ВОЗМОЖНО",
  "damage_severity": "НЕТ" | "ЛЁГКОЕ" | "СРЕДНЕЕ" | "СИЛЬНОЕ" | "КРИТИЧЕСКОЕ" | "НЕИЗВЕСТНО",
  "damage_types": [],
  "visible_evidence": "кратко и конкретно на русском",
  "human_review": true,
  "received_guess": "что фактически видно/опознано"
}}
""".strip()

    payload = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": images,
            }
        ],
        "options": {
            "temperature": 0.1,
            "num_predict": 900,
        },
    }

    r = requests.post(
        ollama_url.rstrip("/") + "/api/chat",
        json=payload,
        timeout=300,
    )
    r.raise_for_status()
    body = r.json()
    text = str(((body.get("message") or {}).get("content")) or "").strip()

    try:
        data = json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            raise RuntimeError("Ollama returned non-JSON response")
        data = json.loads(m.group(0))

    return normalize_result(data)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pending-output", required=True)
    ap.add_argument("--results", required=True)
    ap.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    ap.add_argument("--model", default="qwen2.5vl:3b")
    ap.add_argument("--max-cases", type=int, default=6)
    args = ap.parse_args()

    pending_text = Path(args.pending_output).read_text(encoding="utf-8", errors="replace")
    pending = extract_clasp_return(pending_text)
    if not isinstance(pending, list):
        raise RuntimeError("Pending payload is not a list")

    pending = pending[: max(0, args.max_cases)]
    results: list[dict[str, Any]] = []

    if not pending:
        Path(args.results).write_text("[]\n", encoding="utf-8")
        print("No pending photo cases.")
        return 0

    for idx, case in enumerate(pending, start=1):
        print(
            f"[{idx}/{len(pending)}] "
            f"{case.get('sheet')} row {case.get('row')} "
            f"{case.get('expected')!r}",
            flush=True,
        )
        try:
            result = analyze_case(
                case,
                ollama_url=args.ollama_url,
                model=args.model,
            )
        except Exception as exc:
            result = {
                "verdict": "НЕДОСТАТОЧНО ДАННЫХ",
                "confidence_pct": 0,
                "substitution": "ВОЗМОЖНО",
                "damage": "ВОЗМОЖНО",
                "damage_severity": "НЕИЗВЕСТНО",
                "damage_types": [],
                "visible_evidence": f"Ошибка локального vision-анализа: {exc}",
                "human_review": True,
                "received_guess": "",
            }

        result["sheet"] = str(case.get("sheet") or "")
        result["row"] = int(case.get("row") or 0)
        results.append(result)
        time.sleep(0.2)

    Path(args.results).write_text(
        json.dumps(results, ensure_ascii=False, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    print(f"Prepared {len(results)} results.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
