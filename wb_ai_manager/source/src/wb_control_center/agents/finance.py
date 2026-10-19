from __future__ import annotations

import asyncio
from typing import Any

from .base import BaseAgent
from ..metrics import summarize_for_prompt
from ..models import AgentResult


REPORT_PAGE_SIZE = 500
REPORT_MAX_PAGES = 4
REPORT_PAGE_INTERVAL_SECONDS = 61


def _report_rows(payload: Any) -> list[dict[str, Any]] | None:
    if isinstance(payload, list):
        return payload if all(isinstance(row, dict) for row in payload) else None
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        rows = payload["data"]
        return rows if all(isinstance(row, dict) for row in rows) else None
    return None


def _row_rrd_id(row: dict[str, Any]) -> int | None:
    value = row.get("rrdId", row.get("rrd_id", row.get("rrdid")))
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


class FinanceAgent(BaseAgent):
    name = "finance"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        try:
            balance = await self.call("wb_finance_balance")
            out.snapshots.append(("balance", balance if isinstance(balance, dict) else {"data": balance}))
        except Exception as e:
            out.events.append(self.event("warning", "balance_failed", "Не удалось получить финансовый баланс WB", str(e)))
            balance = None
        start, end = self.dates(7)
        report_rows: list[dict[str, Any]] = []
        seen_rrd_ids: set[int] = set()
        cursor = 0
        pages = 0
        complete = False
        report_error: str | None = None
        for page_number in range(REPORT_MAX_PAGES):
            if page_number:
                await asyncio.sleep(REPORT_PAGE_INTERVAL_SECONDS)
            try:
                page = await self.call(
                    "wb_finance_report",
                    date_from=start,
                    date_to=end,
                    limit=REPORT_PAGE_SIZE,
                    rrd_id=cursor,
                )
            except Exception as e:
                report_error = f"{type(e).__name__}: {e}"
                break
            pages += 1
            if isinstance(page, dict):
                status_code = page.get("status_code", page.get("statusCode"))
                try:
                    status_code = int(status_code or 0)
                except (TypeError, ValueError):
                    status_code = 0
                rows = page.get("data")
                if status_code == 204 and rows is None:
                    complete = True
                    break
                if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                    report_error = "WB вернул неожиданный формат отчёта"
                    break
            else:
                rows = _report_rows(page)
                if rows is None:
                    report_error = "WB вернул неожиданный формат отчёта"
                    break
            if not rows:
                complete = True
                break
            page_ids: list[int] = []
            for row in rows:
                rrd_id = _row_rrd_id(row)
                if rrd_id is None:
                    report_error = "В строке отчёта WB отсутствует курсор rrdId"
                    break
                page_ids.append(rrd_id)
                if rrd_id not in seen_rrd_ids:
                    report_rows.append(row)
                    seen_rrd_ids.add(rrd_id)
            if report_error:
                break
            next_cursor = max(page_ids, default=cursor)
            if next_cursor <= cursor:
                report_error = "Курсор отчёта WB не продвинулся"
                break
            cursor = next_cursor
            if page_number == REPORT_MAX_PAGES - 1:
                report_error = "Достигнут предел страниц за один расчёт периода"
        report = {
            "data": report_rows,
            "complete": complete,
            "status": "complete" if complete else "partial",
            "row_count": len(report_rows),
            "pages": pages,
            "page_size": REPORT_PAGE_SIZE,
            "date_from": start,
            "date_to": end,
            "last_rrd_id": cursor or None,
        }
        if report_error:
            report["error"] = report_error
        out.snapshots.append(("report_7d", report))
        if not complete:
            out.events.append(self.event(
                "warning",
                "finance_report_incomplete",
                "Отчёт реализации WB загружен не полностью",
                f"Получено {len(report_rows)} строк. WB не подтвердил конец отчёта; суммы по нему не считаются.",
                {
                    "row_count": len(report_rows),
                    "pages": pages,
                    "last_rrd_id": cursor or None,
                    "date_from": start,
                    "date_to": end,
                    "error": report_error,
                },
            ))
        if self.ctx.worker is not None and self.ctx.worker.configured:
            try:
                worker_finance = await self.ctx.worker.finance_bundle()
                out.snapshots.append(("worker_finance", worker_finance))
                health = worker_finance.get("source_health") or {}
                if health.get("status") != "FRESH":
                    out.events.append(self.event("warning", "worker_finance_stale", "Финансовый контур wb-api-worker не свежий", str(health), health))
            except Exception as e:
                out.events.append(self.event("warning", "worker_finance_failed", "Не удалось получить сводный финансовый контур wb-api-worker", str(e)))
        if self.ctx.llm.enabled:
            text = await self.ctx.llm.complete(
                "Ты агент ФАКТА финансов Wildberries. Отчёт реализации и начисления — независимая проверка плановой юнитки, а не продолжение её формул. Если report.complete не равно true, считай отчёт частичным и не делай выводы об общих суммах. Найди необычные комиссии, логистику, хранение, штрафы, удержания и расхождения, но не называй плановую прибыль фактической и не объявляй SKU убыточным без сопоставимого закрытого финансового факта. PRIMARY-план: «Юнит-экономика вб / WB FBS новая». Только данные, без домыслов, максимум 7 пунктов.",
                summarize_for_prompt({"balance": balance, "report": report}),
                max_tokens=850,
            )
            if text:
                out.events.append(self.event("info", "finance_analysis", "AI-разбор финансов WB", text))
        return out
