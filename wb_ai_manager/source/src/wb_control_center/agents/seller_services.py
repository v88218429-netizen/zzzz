from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import httpx

from .base import BaseAgent
from ..models import AgentResult


JAM_URL = "https://common-api.wildberries.ru/api/common/v1/subscriptions"
TARIFF_OPTIONS_URL = "https://common-api.wildberries.ru/api/common/v1/tariff-constructor/options"


def _published_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


class SellerServicesAgent(BaseAgent):
    """Read-only monitor for Jam subscription and currently active tariff options."""

    name = "seller_services"

    async def run(self) -> AgentResult:
        out = AgentResult(agent=self.name)
        token = str(getattr(self.ctx.settings, "wb_api_token", "") or "").strip()
        if not token:
            out.snapshots.append(("service_health", {"status": "unavailable", "checked_at": datetime.now(timezone.utc).isoformat(), "error": "WB_API_TOKEN не настроен"}))
            out.events.append(self.event("warning", "services_token_missing", "Подписка и опции WB не подключены", "Для чтения условий продавца нужен токен API WB."))
            return out

        now = datetime.now(timezone.utc)
        checked_at = now.isoformat()
        health: dict[str, Any] = {"checked_at": checked_at, "source": "Wildberries API"}
        async with httpx.AsyncClient(timeout=20) as client:
            for key, url in (("jam_subscription", JAM_URL), ("tariff_options", TARIFF_OPTIONS_URL)):
                try:
                    response = await client.get(url, headers={"Authorization": token}, params={"locale": "ru"} if key == "tariff_options" else None)
                    response.raise_for_status()
                    payload = response.json() if response.text.strip() else None
                    health[key] = {"status": "fresh", "checked_at": checked_at}
                    previous = self.ctx.db.latest_snapshot(self.name, key)
                    previous_data = (previous or {}).get("data")
                    out.snapshots.append((key, {"status": "fresh", "data": payload, "checked_at": checked_at, "source": url}))

                    if previous is None:
                        continue
                    # Database.latest_snapshot returns the persisted wrapper, whose
                    # `data` field contains our API envelope. Compare payloads, not
                    # the wrapper itself, or every poll looks like a change.
                    previous_payload = previous_data.get("data") if isinstance(previous_data, dict) else previous_data
                    if previous_payload != payload:
                        if key == "jam_subscription":
                            out.events.append(self.event("warning", "jam_subscription_changed", "Изменился статус подписки «Джем»", "Проверь даты и уровень подписки в данных WB. Никакие опции не менялись автоматически.", {"before": previous_payload, "current": payload, "source": url, "checked_at": checked_at}))
                        else:
                            commission = payload.get("totalCommissionRate") if isinstance(payload, dict) else None
                            out.events.append(self.event("warning", "tariff_options_changed", "Изменился набор платных опций WB", f"WB вернул обновлённый список подключённых опций. Общая ставка комиссии из ответа: {commission if commission is not None else 'не передана'}. Проверь состав и условия; автоматического изменения нет.", {"before": previous_payload, "current": payload, "source": url, "checked_at": checked_at}))

                    if key == "jam_subscription" and isinstance(payload, dict):
                        state = str(payload.get("state") or "").casefold()
                        till = _published_time(payload.get("till"))
                        days_left = (till - now).total_seconds() / 86400 if till else None
                        if state == "active" and days_left is not None and 0 <= days_left <= 7:
                            out.events.append(self.event("warning", "jam_renewal_soon", "Скоро заканчивается оплаченный период «Джем»", f"В данных WB окончание периода указано как {payload.get('till')}. Это напоминание; продление не выполняется.", {"subscription": payload, "days_left": round(days_left, 2), "source": url, "checked_at": checked_at}))
                except Exception as exc:
                    health[key] = {"status": "unavailable", "checked_at": checked_at, "error": str(exc), "source": url}
                    out.events.append(self.event("warning", f"{key}_unavailable", "Источник условий WB недоступен", f"Не удалось прочитать {key}: {exc}. Последний успешный снимок не перезаписан.", {"source": url, "checked_at": checked_at}))

        out.snapshots.append(("service_health", health))
        return out
