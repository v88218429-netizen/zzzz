# 19 агентов: detection → cross-contour decision

| Агент | Факт | Куда идёт | Тип решения |
|---|---|---|---|
| API Health | токен/API/degradation | Source Guard | запрещает денежные выводы на сломанном источнике |
| Cards | ошибки/блокировки/карантин | Card + Ads | сначала исправить карточку, затем масштабировать |
| Advertising Monitor | spend/orders/DRR/CTR/CPC | Ads + Unit + Funnel + Stock | числовое решение или явное ограничение по данным |
| Advertising Optimizer | bid/budget/recommendations/clusters + 14d stats | Advertising Controller + Safety | точная ставка и лимит расхода после проверки экономики, спроса, запаса и риска; запись в WB запрещена |
| Inventory | stock + all-orders trend + frequency trend + days cover | Demand Forecast + Unit + Ads | прогнозное покрытие и конкретное пополнение; рекламу не меняет сам, а передаёт ограничение Advertising Controller |
| Supply | acceptance coefficients | Supply + localisation | склад-кандидат + localisation guard |
| Funnel | views/cart/orders | Card + Market + Returns | карточка/оффер до покупки дополнительного трафика |
| Search Positions | position delta/query | Search + Stock + Unit + Market + Ads | root cause before bid |
| Price & Margin | prices/promotions | Unit + Ads | promo guard / price economics |
| Finance | balance/reports | Finance evidence | контекст абсолютной прибыли |
| Cost Guard | storage/penalties/deductions | Finance + Quality + FF | устранить повторяемую причину расходов |
| Reviews & Questions | rating/unanswered | Quality + Card | triage + связь повторных проблем с SKU |
| Buyer Chats | customer messages | Quality + Returns + FF | root-cause incident |
| Orders FBS | new/reshipment | FBS + Stock | reconcile status/reserve/stock |
| Returns & Quality | claims/reasons | Quality + Card + Ads | stop-scale + root cause |
| Documents | financial/docs | Evidence layer | подтверждающий источник, не самостоятельный бизнес-совет |
| Competitors | public search/price/rating/reviews | Market + Search + Card | price/offer/card diagnosis |
| Experiments | test discipline | Decision follow-up | одно изменение → окно → stop-condition |
| Supervisor | events/decisions | Executive layer | приоритеты и итоговый список действий |

Инфраструктурные агенты (API Health, Documents, Experiments, Supervisor) не обязаны генерировать отдельное коммерческое действие на каждом цикле. Их задача — качество evidence, безопасность, тестирование и приоритизация.
