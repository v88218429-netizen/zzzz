from __future__ import annotations

from dataclasses import dataclass
from math import ceil
from typing import Any

from .models import DecisionCard
from .advertising_controller import AdvertisingController
from .demand_forecast import build_demand_forecast


def _n(v: Any) -> float | None:
    try:
        if v is None: return None
        return float(v)
    except Exception:
        return None


def _ev(source: str, metric: str, value: Any, note: str = "") -> dict[str, Any]:
    return {"source": source, "metric": metric, "value": value, "note": note}


def _snap(snapshots: dict[str, Any], source: str, key: str) -> Any:
    """Return latest snapshot payload with wrapper layers removed conservatively."""
    try:
        item = snapshots[source][key]
    except Exception:
        return None
    if isinstance(item, dict) and "data" in item and "created_at" in item:
        return item.get("data")
    return item


def _rows(value: Any, preferred: tuple[str, ...] = ("data", "items", "orders", "claims", "events", "campaigns")) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [x for x in value if isinstance(x, dict)]
    if not isinstance(value, dict):
        return []
    for key in preferred:
        candidate=value.get(key)
        if isinstance(candidate,list):
            return [x for x in candidate if isinstance(x,dict)]
    return []

def _pick_num(row: dict[str, Any], *keys: str) -> float | None:
    for k in keys:
        if k in row:
            n=_n(row.get(k))
            if n is not None:
                return n
    return None


@dataclass
class DecisionEngine:
    """Cross-contour rule engine.

    The important rule is architectural: detector agents may emit facts, but the final
    recommendation is produced only here after economics, demand, stock, advertising,
    search and data-quality evidence are considered together.
    """
    policy: Any

    def build(self, portfolio: dict[str, Any], snapshots: dict[str, Any] | None = None) -> list[DecisionCard]:
        snapshots = snapshots or {}
        out: list[DecisionCard] = []
        current_portfolio = bool(portfolio.get("current_data", portfolio.get("data_origin") != "seeded_real_facts"))
        safe_portfolio = portfolio if current_portfolio else {
            "data_origin": portfolio.get("data_origin"),
            "current_data": False,
            "source_health": portfolio.get("source_health") or [],
            "stores": [],
            "own_27": {},
            "portfolio": {},
        }
        out += self._source_quality(portfolio)
        if not current_portfolio:
            out.append(DecisionCard(
                decision_key="data:portfolio_historical_only",
                scope="system",
                entity_id="portfolio",
                title="Портфельный срез устарел — текущие решения по нему заблокированы",
                diagnosis=str(portfolio.get("stale_reason") or "Доступен только исторический срез портфеля."),
                priority="high",
                confidence="high",
                recommended_actions=[{
                    "step": 1,
                    "action": "Не использовать архивный срез для текущих цен, рекламы и поставок. Работать только по свежим WB/FBS/finance источникам до восстановления живого портфеля.",
                    "mode": "automatic_policy",
                }],
                evidence=[_ev("portfolio", "period", portfolio.get("period")), _ev("portfolio", "origin", portfolio.get("data_origin"))],
                blockers=["Нет свежего live-среза портфеля по выбранному периоду."],
                follow_up="Снять блокировку автоматически после появления свежего live-среза.",
            ))
        if current_portfolio:
            out += self._operating_findings(snapshots)
            out += self._store_decisions(portfolio)
            out += self._product_decisions(portfolio)
            out += self._inventory_decisions(portfolio)
        out += self._advertising_control_decisions(safe_portfolio, snapshots)
        out += self._card_content_decisions(safe_portfolio, snapshots)
        out += self._operational_decisions(safe_portfolio, snapshots)
        out += self._event_decisions(safe_portfolio, snapshots)
        out += self._search_market_decisions(safe_portfolio, snapshots)
        # Stable de-dup by key, retaining the highest priority version.
        rank={"low":1,"medium":2,"high":3,"critical":4}
        best: dict[str, DecisionCard] = {}
        for d in out:
            if d.decision_key not in best or rank[d.priority] > rank[best[d.decision_key].priority]:
                best[d.decision_key]=d
        return sorted(best.values(), key=lambda d:(-rank[d.priority], d.title))


    def _operating_findings(self, snapshots: dict[str, Any]) -> list[DecisionCard]:
        out: list[DecisionCard] = []
        rows = snapshots.get("_operating_findings") or []
        if not isinstance(rows, list):
            return out
        for r in rows:
            if not isinstance(r, dict):
                continue
            level = str(r.get("level") or "medium")
            if level not in {"low","medium","high","critical"}:
                level="medium"
            out.append(DecisionCard(
                decision_key=str(r.get("key") or "operating:finding"),
                scope="operating_model", entity_id=str(r.get("key") or "model"),
                title=str(r.get("title") or "Операционная закономерность"),
                diagnosis=str(r.get("conclusion") or ""), priority=level,
                confidence=str(r.get("confidence") or "high") if str(r.get("confidence") or "high") in {"low","medium","high"} else "high",
                recommended_actions=[{"step":1,"action":str(r.get("action") or "Разобрать причину по связанным данным"),"mode":"operating_model"}],
                evidence=[_ev("trusted_tables", str(x.get("metric") or "metric"), x.get("value")) for x in (r.get("evidence") or []) if isinstance(x,dict)],
                follow_up="Пересчитать после следующего обновления trusted-таблиц."
            ))
        return out

    def _source_quality(self, p: dict[str, Any]) -> list[DecisionCard]:
        bad=[x for x in p.get('source_health',[]) if x.get('status') in {'broken','stale'}]
        if not bad: return []
        critical=[x for x in bad if x.get('status')=='broken']
        return [DecisionCard(
            decision_key='data:source_quality', scope='system', entity_id='sources',
            title='Не использовать слабые источники как основание для денег',
            diagnosis='Часть таблиц устарела или возвращает некорректный срез. Решения должны идти из live WB / 27 / свежей сводки, а слабые источники — только как справка.',
            priority='high' if critical else 'medium', confidence='high',
            recommended_actions=[
                {"step":1,"action":"Исключить broken/stale источники из финансовых решений","mode":"automatic_policy"},
                {"step":2,"action":"Продолжать работу на более свежем trusted-источнике","mode":"automatic_policy"},
                {"step":3,"action":"Восстановить источник в фоне и вернуть его только после проверки свежести","mode":"maintenance"},
            ],
            evidence=[_ev(x.get('id','source'),'status',x.get('status'),f"freshness={x.get('freshness')}; trust={x.get('trust')}") for x in bad[:8]],
            follow_up='Перепроверять свежесть при каждом цикле импорта.'
        )]

    def _store_decisions(self, p: dict[str, Any]) -> list[DecisionCard]:
        out=[]
        adcfg=self.policy.thresholds.get('advertising',{})
        warn=float(adcfg.get('warn_drr_pct',12))
        for s in p.get('stores',[]):
            drr=_n(s.get('drr_pct')); margin=_n(s.get('margin_pct')); profit=_n(s.get('profit_rub')); buyouts=_n(s.get('buyouts_qty')); orders=_n(s.get('orders_qty'))
            buyout_rate=(buyouts/orders*100) if orders and buyouts is not None else None
            groups=s.get('groups') or []
            negative=[g for g in groups if (_n(g.get('profit_rub')) or 0) < 0]
            if drr is not None and drr>=warn and margin is not None:
                priority='critical' if margin<=0 or drr>=20 else 'high'
                why=f"ДРР {drr:.1f}% при марже {margin:.1f}%"
                if buyout_rate is not None: why+=f", текущий выкуп по срезу ≈{buyout_rate:.1f}%"
                actions=[
                    {"step":1,"action":"Не увеличивать рекламный трафик магазина до разложения по SKU/группам","mode":"read_only_recommendation"},
                    {"step":2,"action":"Отделить убыточные группы от прибыльных и проверить, где проблема: выкуп, цена, карточка, логистика или реклама","mode":"analysis"},
                    {"step":3,"action":"Для убыточных групп сформировать отдельный план: прекратить наращивание платного спроса → исправить экономику/выкуп → провести повторную проверку","mode":"analysis"},
                ]
                if negative:
                    actions.insert(1,{"step":2,"action":"В первую очередь разобрать: "+', '.join(str(x.get('name')) for x in negative[:5]),"mode":"analysis"})
                out.append(DecisionCard(
                    decision_key=f"store:{s.get('id')}:ads_economics", scope='store', entity_id=str(s.get('id')),
                    title=f"{s.get('name')}: рекламу нельзя оценивать отдельно от экономики",
                    diagnosis=why+'. Решение по ставке нельзя делать из календарного сравнения заказов и выкупов: выкупы запаздывают. Нужны юнитка, кампания и зрелость той же когорты заказов.',
                    priority=priority, confidence='high' if s.get('source') else 'medium', recommended_actions=actions,
                    evidence=[_ev(s.get('source','trusted'),'ДРР',drr),_ev(s.get('source','trusted'),'Маржа',margin),_ev(s.get('source','trusted'),'Прибыль',profit),_ev(s.get('source','trusted'),'Выкуп %',buyout_rate)],
                    follow_up='После исправления пересчитать прибыль, ДРР и выкуп на следующем сопоставимом окне.'
                ))

            if negative:
                worst=sorted(negative,key=lambda x:_n(x.get('profit_rub')) or 0)[:5]
                out.append(DecisionCard(
                    decision_key=f"store:{s.get('id')}:negative_groups", scope='store', entity_id=str(s.get('id')),
                    title=f"{s.get('name')}: прошлый реализованный срез содержит убыточные группы",
                    diagnosis=(
                        'Это результат уже созревших более ранних когорт. Его нельзя напрямую приписывать свежим заказам текущей недели. '
                        'Он нужен как сигнал для разбора причин и обучения рекламной модели, но не как автоматический запрет текущего спроса.'
                    ),
                    priority='high', confidence='high',
                    recommended_actions=[
                        {"step":1,"action":"Разобрать убыточные группы по SKU и определить, какие именно старые когорты дали убыток","mode":"cohort_analysis"},
                        {"step":2,"action":"Для текущих рекламных решений опираться на юнитку SKU, статистику кампании, спрос, позицию и зрелость свежей когорты","mode":"analysis"},
                        {"step":3,"action":"После созревания новых заказов сравнить фактический результат той же когорты с прогнозом","mode":"cohort_followup"},
                    ],
                    evidence=[_ev(s.get('source','weekly'),str(g.get('name')),f"realised_profit={g.get('profit_rub')}; realised_margin={g.get('margin_pct')}") for g in worst],
                    follow_up='Пересчитать после созревания сопоставимой когорты заказов.'
                ))
        return out

    def _product_decisions(self, p: dict[str, Any]) -> list[DecisionCard]:
        out=[]; own=p.get('own_27') or {}
        for x in own.get('products',[])[:500]:
            sku=str(x.get('sku') or '')
            if not sku: continue
            profit=_n(x.get('profit_rub')); margin=_n(x.get('margin_pct')); drr=_n(x.get('drr_pct')); price=_n(x.get('price_rub'))
            if profit is not None and profit<0:
                actions=[
                    {"step":1,"action":"Не повышать ставку/бюджет рекламы для этого SKU","mode":"policy"},
                    {"step":2,"action":"Проверить, что именно делает единицу убыточной: ДРР, комиссия, логистика, себестоимость, цена","mode":"analysis"},
                ]
                targets=[_n(x.get('target_price_profit_rub')), _n(x.get('target_price_margin_rub')), _n(x.get('target_price_roi_rub'))]
                viable=sorted(t for t in targets if t is not None and price is not None and t > price)
                if viable and price:
                    target=viable[0]
                    actions.append({"step":3,"action":f"Конкретный ценовой тест: {price:.0f} → {target:.0f} ₽. До результата теста рекламу не масштабировать.","mode":"price_test"})
                elif drr is not None and price and price > 0:
                    break_even_drr=max(0.0, drr + (profit / price * 100.0))
                    target_drr=max(0.0, break_even_drr - 1.0)
                    actions.append({"step":3,"action":f"Цена не даёт подтверждённого безопасного сценария. Снизить рекламную нагрузку до ДРР не выше ≈{target_drr:.1f}% (расчётный безубыточный ≈{break_even_drr:.1f}%) и повторно проверить прибыль.","mode":"advertising_control"})
                else:
                    actions.append({"step":3,"action":"Денежное решение заблокировано: данных недостаточно для расчёта безопасной цены или рекламного ДРР.","mode":"blocked"})
                out.append(DecisionCard(
                    decision_key=f"sku:{sku}:negative_unit", scope='sku', entity_id=sku,
                    title=f"{x.get('name')}: продажа убыточна по текущей юнитке",
                    diagnosis=f"Прибыль на единицу {profit:.2f} ₽" + (f", маржа {margin:.1f}%" if margin is not None else '') + (f", ДРР {drr:.1f}%" if drr is not None else '') + '. Увеличение рекламы сейчас масштабирует убыток.',
                    priority='critical', confidence='high', recommended_actions=actions,
                    evidence=[_ev('27/Юнитка','price_rub',price),_ev('27/Юнитка','profit_rub',profit),_ev('27/Юнитка','margin_pct',margin),_ev('27/Юнитка','drr_pct',drr)],
                    follow_up='Повторно разрешать масштабирование только после положительной экономики на достаточной выборке.'
                ))
            elif margin is not None and margin < 5 and drr is not None and drr > 8:
                out.append(DecisionCard(
                    decision_key=f"sku:{sku}:thin_margin_ads", scope='sku', entity_id=sku,
                    title=f"{x.get('name')}: слишком тонкий запас маржи для текущей рекламы",
                    diagnosis=f"Маржа {margin:.1f}% при ДРР {drr:.1f}%. Небольшое ухудшение CPC/CR может сделать SKU убыточным.",
                    priority='high', confidence='high',
                    recommended_actions=[
                        {"step":1,"action":"Не масштабировать бюджет до проверки 3–7 дней","mode":"policy"},
                        {"step":2,"action":"Искать рост прибыли через CR/цену/органику, а не через голое повышение ставки","mode":"analysis"},
                        {"step":3,"action":"Задать стоп-условие: отрицательная прибыль или дальнейшее ухудшение ДРР","mode":"experiment"},
                    ], evidence=[_ev('27/Юнитка','margin_pct',margin),_ev('27/Юнитка','drr_pct',drr)],
                    follow_up='Сравнить абсолютную прибыль до/после теста.'
                ))
        return out

    def _inventory_decisions(self, p: dict[str, Any]) -> list[DecisionCard]:
        out=[]; own=p.get('own_27') or {}
        invcfg=self.policy.thresholds.get('inventory',{})
        runtime_ad=((self.policy.raw.get('runtime') or {}).get('advertising') or {})
        critical=float(invcfg.get('critical_days_cover',2))
        warning=float(runtime_ad.get('min_stock_days_for_hold', invcfg.get('warning_days_cover',5)))
        target=float(runtime_ad.get('target_stock_days', invcfg.get('target_days_cover',14)))
        max_trend=float(runtime_ad.get('max_trend_pct_for_forecast',60))
        over=float(invcfg.get('overstock_days_cover',75))
        for x in own.get('products',[])[:500]:
            daily=_n(x.get('orders_per_day')); stock=_n(x.get('safe_stock'))
            if not daily or daily<=0 or stock is None: continue
            planned_incoming=max(0.0, _n(x.get('planned_incoming_qty')) or 0.0)
            incoming_confirmed=bool(x.get('planned_incoming_confirmed'))
            effective_stock=stock + (planned_incoming if incoming_confirmed else 0.0)

            # Один прогноз спроса используется и здесь, и в рекламном контуре. Так
            # запас и реклама не получают разные версии будущего спроса.
            demand = build_demand_forecast(x, max_growth_pct=max_trend)
            forecast_daily = demand.forecast_orders_1d if demand.forecast_orders_1d is not None else daily
            order_trend = demand.order_acceleration_pct
            freq_trend = demand.search_frequency_trend_pct
            demand_growth = order_trend if order_trend is not None else 0.0
            days=effective_stock/forecast_daily if forecast_daily and forecast_daily>0 else effective_stock/daily
            debt=max(0,_n(x.get('fbs_debt_orders')) or 0)
            trend_note=(f"; прогнозный темп ≈{forecast_daily:.1f}/день" + (f" ({demand_growth:+.1f}% к базовому)" if order_trend is not None else ""))

            if days <= warning:
                profit=_n(x.get('profit_rub')); margin=_n(x.get('margin_pct')); drr=_n(x.get('drr_pct'))
                pri='critical' if days<=critical else 'high'
                source_conf='high' if str(x.get('safe_stock_source','')).upper() in {'K2 SAFE','FF','K2'} else 'medium'
                if profit is not None and profit < 0:
                    bridge_days=max(3.0, critical + 1.0)
                    bridge_need=max(0,ceil(forecast_daily*bridge_days + debt - stock))
                    title=f"{x.get('name')}: запас низкий, но юнитка отрицательная — нельзя закупать на полный горизонт"
                    diagnosis=(f"Покрытие ≈{days:.1f} дня при текущем темпе ≈{daily:.1f}/день{trend_note}, но прибыль на единицу {profit:.2f} ₽"
                               + (f" и маржа {margin:.1f}%" if margin is not None else '')
                               + f'. Пополнение на {target:.0f} дней сейчас масштабирует не только продажи, но и убыток.')
                    actions=[
                        {"step":1,"action":"Сначала исправить экономику SKU: цена → комиссия/логистика → ДРР → себестоимость → выкуп","mode":"unit_economics_guard"},
                        {"step":2,"action":f"Если дефицит недопустим, пополнить не более чем примерно до {bridge_days:.0f} дней: ориентир {bridge_need} шт. по прогнозному темпу {forecast_daily:.1f}/день","mode":"bridge_supply"},
                        {"step":3,"action":"Рекламное решение брать только из рекламного контура этого товара; отрицательная юнитка запрещает повышение ставки","mode":"advertising_control_link"},
                    ]
                    ev=[_ev('27/Сводная','safe_stock',stock,x.get('safe_stock_source','')),_ev('27/Сводная','orders_per_day',daily),_ev('demand_forecast','forecast_orders_per_day',round(forecast_daily,2)),_ev('demand_forecast','orders_trend_pct',order_trend),_ev('search','search_frequency_trend_pct',freq_trend),_ev('27/Юнитка','profit_rub',profit),_ev('27/Юнитка','margin_pct',margin),_ev('27/Юнитка','drr_pct',drr)]
                else:
                    need=max(0,ceil(forecast_daily*target + debt - effective_stock))
                    if need == 0:
                        title=f"{x.get('name')}: низкий остаток, но прогноз не подтверждает объём поставки"
                        diagnosis=(f"Безопасный остаток {stock:.0f} шт., текущий темп ≈{daily:.1f}/день, "
                                   f"прогноз ≈{forecast_daily:.3f}/день. Расчёт на {target:.0f} дней даёт нулевую потребность; "
                                   "нельзя показывать это как распоряжение поставить 0 шт. Расхождение темпов требует проверки.")
                        actions=[
                            {"step":1,"action":"Сверить заказы последних 7 дней, остаток K2/ФФ и доступность карточки; выяснить, почему прогноз близок к нулю","mode":"demand_validation"},
                            {"step":2,"action":"Не оформлять поставку по нулевому расчёту. До проверки не увеличивать рекламный спрос на товар с низким остатком","mode":"supply_guard"},
                            {"step":3,"action":"После подтверждения спроса пересчитать количество для целевого покрытия и срок поступления","mode":"follow_up"},
                        ]
                    else:
                        title=f"{x.get('name')}: риск дефицита — нужен конкретный план пополнения"
                        diagnosis=f"Безопасный остаток {stock:.0f} шт., текущий темп ≈{daily:.1f}/день{trend_note}, прогнозное покрытие ≈{days:.1f} дня. Для цели {target:.0f} дней ориентировочно не хватает {need} шт." + (f" В плане отмечено ещё {planned_incoming:.0f} шт., но приход не считается доступным, пока не подтверждена дата/приёмка." if planned_incoming and not incoming_confirmed else '')
                        actions=[
                            {"step":1,"action":f"Поставить/произвести ориентировочно {need} шт. до целевого покрытия {target:.0f} дней при прогнозном темпе {forecast_daily:.1f}/день","mode":"supply_plan"},
                            {"step":2,"action":"Остаточный контур сам ставку не меняет. Рекламный контур должен пересчитать точную ставку и предел расхода с учётом этого прогноза запаса","mode":"advertising_control_link"},
                            {"step":3,"action":"После нового снимка K2/ФФ пересчитать прогноз спроса, покрытие и рекламный коридор одновременно","mode":"follow_up"},
                        ]
                    if planned_incoming and not incoming_confirmed:
                        actions.insert(1,{"step":2,"action":f"Подтвердить дату и фактический приход запланированных {planned_incoming:.0f} шт.; до подтверждения не вычитать их из потребности и не использовать для разгона рекламы","mode":"incoming_supply_guard"})
                        for i,a in enumerate(actions,1): a["step"]=i
                    ev=[_ev('27/Сводная','safe_stock',stock,x.get('safe_stock_source','')), _ev('27/Сводная','orders_per_day',daily), _ev('demand_forecast','forecast_orders_per_day',round(forecast_daily,2)), _ev('demand_forecast','demand_growth_pct',round(demand_growth,2)), _ev('demand_forecast','orders_trend_pct',order_trend), _ev('search','search_frequency_trend_pct',freq_trend), _ev('derived','forecast_days_cover',round(days,1)), _ev('27/Сводная','fbs_debt_orders',debt),_ev('27/Юнитка','profit_rub',profit),_ev('27/Юнитка','margin_pct',margin)]
                out.append(DecisionCard(
                    decision_key=f"sku:{x.get('sku')}:stockout", scope='sku', entity_id=str(x.get('sku')),
                    title=title, diagnosis=diagnosis, priority=pri, confidence=source_conf,
                    recommended_actions=actions, evidence=ev,
                    follow_up='Пересчитывать при каждом новом снимке K2/ФФ, изменении темпа заказов, частотности или экономики.'
                ))
            elif days >= over:
                out.append(DecisionCard(
                    decision_key=f"sku:{x.get('sku')}:overstock", scope='sku', entity_id=str(x.get('sku')),
                    title=f"{x.get('name')}: капитал заморожен в избыточном запасе",
                    diagnosis=f"Прогнозное покрытие ≈{days:.0f} дней при текущем темпе {daily:.1f}/день{trend_note}. Новую закупку лучше не делать, пока спрос не догонит запас.",
                    priority='medium', confidence='medium',
                    recommended_actions=[
                        {"step":1,"action":"Остановить дополнительное пополнение этого SKU","mode":"supply_guard"},
                        {"step":2,"action":"Если юнитка положительная — рекламный контур может проверять дополнительный спрос только в пределах рассчитанной экономики и разрешённого шага ставки","mode":"advertising_control_link"},
                        {"step":3,"action":"Если юнитка слабая — сначала исправить экономику, не демпинговать автоматически","mode":"analysis"},
                    ], evidence=[_ev('27/Сводная','safe_stock',stock),_ev('27/Сводная','orders_per_day',daily),_ev('demand_forecast','forecast_orders_per_day',round(forecast_daily,2)),_ev('derived','forecast_days_cover',round(days,1))],
                    follow_up='Проверять темп заказов и поисковый спрос при каждом цикле; не ждать конца недели при резком ускорении.'
                ))
        return out

    def _advertising_control_decisions(self, p: dict[str, Any], snapshots: dict[str, Any]) -> list[DecisionCard]:
        """Numerical advertising plans for an exact campaign + nmID pair.

        Campaign totals are acceptable only for a single-SKU campaign.  In a multi-SKU
        campaign an exact SKU statistic is mandatory; otherwise the plan is a data
        blocker rather than a guessed money recommendation.
        """
        out: list[DecisionCard] = []
        runtime = self.policy.raw.get("runtime") or {}
        base_cfg = dict(runtime.get("advertising") or {})
        group_overrides = runtime.get("group_overrides") if isinstance(runtime.get("group_overrides"), dict) else {}
        sku_overrides = runtime.get("sku_overrides") if isinstance(runtime.get("sku_overrides"), dict) else {}
        products = {str(x.get("sku")): x for x in (p.get("own_27") or {}).get("products", []) if isinstance(x, dict) and x.get("sku")}

        deep = _snap(snapshots, "advertising_optimizer", "deep_scan")
        deep_rows = []
        if isinstance(deep, dict):
            deep_rows = [x for x in deep.get("campaigns", []) if isinstance(x, dict)]

        if not deep_rows:
            camps = _rows(_snap(snapshots, "advertising_monitor", "active_campaigns"))
            stats = _rows(_snap(snapshots, "advertising_monitor", "stats_7d"))
            smap = {str(x.get("advertId") or x.get("advert_id") or x.get("id") or ""): x for x in stats}
            for c in camps:
                cid = str(c.get("advertId") or c.get("advert_id") or c.get("id") or "")
                nm_ids = []
                for key in ("nmIds", "nm_ids", "nms"):
                    if isinstance(c.get(key), list):
                        nm_ids = [int(x) for x in c[key] if str(x).isdigit()]
                        break
                deep_rows.append({"campaign": c, "campaign_id": cid, "nm_ids": nm_ids, "stats": smap.get(cid, {}), "stats_by_nm": {}, "recommendations": {}, "budget": {}})

        for row in deep_rows:
            campaign = row.get("campaign") if isinstance(row.get("campaign"), dict) else {}
            cid = str(row.get("campaign_id") or campaign.get("advertId") or campaign.get("advert_id") or campaign.get("id") or "")
            nm_ids = [int(x) for x in row.get("nm_ids", []) if str(x).isdigit()] if isinstance(row.get("nm_ids"), list) else []
            campaign_stats = row.get("stats") if isinstance(row.get("stats"), dict) else {}
            stats_by_nm = row.get("stats_by_nm") if isinstance(row.get("stats_by_nm"), dict) else {}
            recs = row.get("recommendations") if isinstance(row.get("recommendations"), dict) else {}
            budget = row.get("budget") if isinstance(row.get("budget"), dict) else {}

            # If a campaign exposes no nm list, retain one diagnostic card, but do not
            # manufacture a SKU association.
            targets: list[int | None] = nm_ids if nm_ids else [None]
            for nm in targets:
                product = products.get(str(nm)) if nm is not None else None
                exact_stats = stats_by_nm.get(str(nm)) if nm is not None and isinstance(stats_by_nm.get(str(nm)), dict) else None
                stats = exact_stats or (campaign_stats if len(nm_ids) <= 1 else {})
                cfg = dict(base_cfg)
                if product is not None:
                    group_name = str(product.get("weekly_group") or "")
                    if group_name and isinstance(group_overrides.get(group_name), dict):
                        cfg.update(group_overrides[group_name])
                    if isinstance(sku_overrides.get(str(nm)), dict):
                        cfg.update(sku_overrides[str(nm)])
                controller = AdvertisingController(cfg)
                reco = recs.get(str(nm), {}) if nm is not None else {}
                plan = controller.build_plan(campaign, stats, product=product, bid_recommendation=reco, campaign_budget=budget)

                extra_blockers = list(plan.blockers)
                if nm is None:
                    extra_blockers.append("кампания не содержит подтверждённой связи с nmID")
                if len(nm_ids) > 1 and exact_stats is None:
                    extra_blockers.append("WB не дал отдельную статистику этого nmID внутри многотоварной кампании; общие цифры кампании к товару не приписываются")
                if nm is not None and product is None:
                    extra_blockers.append("nmID отсутствует в доверенной юнитке/товарном реестре")
                extra_blockers = list(dict.fromkeys(x for x in extra_blockers if x))

                # A multi-SKU card without exact stats is diagnostic only. Never surface
                # a numerical bid change derived from zero/foreign campaign totals.
                cur = plan.current_bid_rub
                tgt = plan.target_bid_rub
                action_text = plan.action_text
                confidence = plan.confidence
                if len(nm_ids) > 1 and exact_stats is None:
                    tgt = cur
                    action_text = "Ставку этого товара не менять до получения отдельной статистики nmID внутри кампании."
                    confidence = "low"

                actions: list[dict[str, Any]] = [{
                    "step": 1,
                    "action": action_text + " Текущая версия ничего в кабинете WB не меняет.",
                    "mode": "numeric_ad_plan",
                    "value": {"current_bid_rub": cur, "target_bid_rub": tgt, "change_pct": (0.0 if tgt == cur and cur is not None else plan.bid_change_pct), "current_spend_24h_rub": plan.current_spend_24h_rub, "max_spend_24h_rub": plan.max_spend_next_24h_rub},
                }]
                if plan.reasons:
                    actions.append({"step":2,"action":"Основание: " + " ".join(plan.reasons[:3]),"mode":"reasoning"})
                actions.append({"step":3,"action":plan.observation_rule,"mode":"recheck_rule"})
                if extra_blockers:
                    actions.append({"step":4,"action":"Для точного решения не хватает: " + " ".join(extra_blockers),"mode":"data_guard"})

                name = str(campaign.get("name") or f"Кампания {cid}")
                sku_label = f" · nmID {nm}" if nm is not None else ""
                diagnosis_bits = [plan.decision_label]
                if len(nm_ids) > 1 and exact_stats is None:
                    diagnosis_bits = ["нет подтверждённой SKU-статистики — денежное решение заблокировано"]
                if plan.observed_drr_pct is not None: diagnosis_bits.append(f"ДРР {plan.observed_drr_pct:.1f}% при допустимом потолке ≤{plan.target_drr_pct:.1f}%")
                if plan.orders_trend_pct is not None: diagnosis_bits.append(f"тренд заказов {plan.orders_trend_pct:+.1f}%")
                if plan.traffic_trend_pct is not None: diagnosis_bits.append(f"тренд трафика {plan.traffic_trend_pct:+.1f}%")
                if plan.stock_days_forecast is not None: diagnosis_bits.append(f"прогноз покрытия {plan.stock_days_forecast:.1f} дн.")
                if plan.cohort_maturity_pct is not None: diagnosis_bits.append(f"свежая когорта созрела примерно на {plan.cohort_maturity_pct:.0f}%")
                if plan.economic_max_drr_pct is not None: diagnosis_bits.append(f"экономический потолок ДРР ≈{plan.economic_max_drr_pct:.1f}%")

                priority = "critical" if plan.decision == "PAUSE_REVIEW" else ("high" if plan.decision in {"SCALE_DOWN","CAP_DEMAND"} else "medium")
                if extra_blockers and confidence == "low" and plan.decision != "PAUSE_REVIEW":
                    priority = "medium"
                evidence = [
                    _ev("WB Promotion", "campaign_id", cid, name),
                    _ev("WB Promotion", "nm_id", nm),
                    _ev("WB Promotion", "sku_stats_exact", bool(exact_stats) or len(nm_ids) <= 1),
                    _ev("расчёт", "решение", plan.decision_label),
                    _ev("WB Promotion", "current_bid_rub", cur),
                    _ev("расчёт", "target_bid_rub", tgt),
                    _ev("WB Promotion", "current_spend_24h_rub", plan.current_spend_24h_rub),
                    _ev("расчёт", "max_spend_next_24h_rub", plan.max_spend_next_24h_rub),
                    _ev("расчёт", "target_drr_pct", plan.target_drr_pct),
                    _ev("расчёт", "max_ad_cost_per_order_rub", plan.max_ad_cost_per_order_rub),
                    _ev("расчёт", "economic_max_drr_pct", plan.economic_max_drr_pct),
                    _ev("WB Promotion", "observed_drr_pct", plan.observed_drr_pct),
                    _ev("trend", "orders_trend_pct", plan.orders_trend_pct),
                    _ev("trend", "traffic_trend_pct", plan.traffic_trend_pct),
                    _ev("27/Сводная", "stock_days_forecast", plan.stock_days_forecast),
                    _ev("27/_WB_ORDER_FEED", "cohort_maturity_pct", plan.cohort_maturity_pct),
                    _ev("27/_WB_ORDER_FEED", "cohort_open_orders_7d", plan.cohort_open_orders_7d),
                    _ev("27/_WB_ORDER_FEED", "buyout_lag_p50_days", plan.buyout_lag_p50_days),
                    _ev("27/_WB_ORDER_FEED", "buyout_lag_p90_days", plan.buyout_lag_p90_days),
                    _ev("WB recommendations", "competitive_bid_rub", plan.wb_competitive_bid_rub),
                ]
                for reason in plan.reasons:
                    evidence.append(_ev("расчёт", "причина", reason))

                suffix = "zero_orders" if plan.decision == "PAUSE_REVIEW" else "numeric_control"
                if nm is None:
                    decision_key = f"advert:{cid}:{suffix}"
                    scope = "campaign"
                    entity_id = cid
                else:
                    decision_key = f"advert:{cid}:{nm}:{suffix}"
                    scope = "campaign_sku"
                    entity_id = f"{cid}:{nm}"
                out.append(DecisionCard(
                    decision_key=decision_key,
                    scope=scope, entity_id=entity_id,
                    title=f"{name}{sku_label}: {diagnosis_bits[0]}",
                    diagnosis=" · ".join(diagnosis_bits),
                    priority=priority, confidence=confidence,
                    recommended_actions=actions,
                    evidence=evidence,
                    blockers=extra_blockers,
                    follow_up=plan.observation_rule,
                ))
        return out


    def _card_content_decisions(self, p: dict[str, Any], snapshots: dict[str, Any]) -> list[DecisionCard]:
        """Find content/card opportunities without pretending that more photos cause sales.

        A content recommendation appears only when a weak funnel signal and an observable
        content gap coexist.  The next step is an experiment, not an asserted causal fix.
        """
        cards = _rows(_snap(snapshots, "cards", "card_catalog"), ("cards","data","items"))
        funnel = _rows(_snap(snapshots, "funnel", "funnel_7d"), ("items","data"))
        if not cards or not funnel:
            return []
        funnel_by_nm: dict[str, dict[str, Any]] = {}
        convs=[]
        for r in funnel:
            nm = str(r.get("nmID") or r.get("nmId") or r.get("nm_id") or "")
            if not nm: continue
            funnel_by_nm[nm]=r
            conv = r.get("conversions") if isinstance(r.get("conversions"),dict) else {}
            atc=_pick_num(conv,"addToCartPercent","add_to_cart_percent") or _pick_num(r,"addToCartPercent","add_to_cart_percent")
            if atc is not None: convs.append(atc)
        if not convs:
            return []
        convs_sorted=sorted(convs)
        median_conv=convs_sorted[len(convs_sorted)//2]
        media_counts=[]
        parsed=[]
        for c in cards:
            nm=str(c.get("nmID") or c.get("nmId") or c.get("nm_id") or "")
            if not nm: continue
            media=None
            for key in ("mediaFiles","photos","media"):
                if isinstance(c.get(key),list): media=len(c[key]); break
            if media is not None: media_counts.append(media)
            parsed.append((nm,c,media))
        if not media_counts:
            return []
        media_sorted=sorted(media_counts)
        median_media=media_sorted[len(media_sorted)//2]
        high_media=max(media_counts)
        out=[]
        for nm,c,media in parsed:
            f=funnel_by_nm.get(nm)
            if not f or media is None: continue
            conv=f.get("conversions") if isinstance(f.get("conversions"),dict) else {}
            atc=_pick_num(conv,"addToCartPercent","add_to_cart_percent") or _pick_num(f,"addToCartPercent","add_to_cart_percent")
            opens=_pick_num(f,"openCardCount","open_card_count","views")
            if atc is None or opens is None or opens < 200: continue
            if atc >= median_conv or media >= median_media:
                continue
            title=str(c.get("title") or c.get("vendorCode") or f"nmID {nm}")
            out.append(DecisionCard(
                decision_key=f"content:{nm}:gallery_experiment",
                scope="sku", entity_id=nm,
                title=f"{title}: проверить фотогалерею как причину слабой карточки",
                diagnosis=f"Переход в корзину {atc:.1f}% ниже медианы наблюдаемого портфеля {median_conv:.1f}%, при этом в карточке {media} медиа против медианы {median_media} (максимум среди наблюдаемых карточек {high_media}). Это корреляция, не доказанная причина.",
                priority="medium", confidence="medium",
                recommended_actions=[
                    {"step":1,"action":"Собрать недостающие полезные слайды по возражениям покупателя: размеры, комплект, применение, материал, упаковка, сравнение вариантов. Не добавлять дубли ради количества.","mode":"content_hypothesis"},
                    {"step":2,"action":"Провести А/Б-тест главного фото/галереи и заранее зафиксировать метрики: CTR, переход в корзину, заказ. Менять одну гипотезу за тест.","mode":"experiment"},
                    {"step":3,"action":"Если конверсия не улучшилась на достаточной выборке, не считать число фото причиной и перейти к цене/офферу/отзывам/качеству.","mode":"recheck_rule"},
                ],
                evidence=[_ev("WB Content","media_count",media),_ev("портфель","median_media_count",median_media),_ev("WB Analytics","add_to_cart_pct",atc),_ev("портфель","median_add_to_cart_pct",median_conv),_ev("WB Analytics","open_card_count",opens)],
                follow_up="Оценивать только по результату А/Б-теста и достаточной выборке, а не по факту заполнения слотов.",
            ))
        return out

    def _operational_decisions(self, p: dict[str, Any], snapshots: dict[str, Any]) -> list[DecisionCard]:
        """Turn the remaining detector snapshots into cross-contour operational plans.

        These rules are deliberately conservative: when the missing link (for example
        campaign→SKU or SKU unit economics) is not proven, the card says exactly what
        must be resolved before a money-changing recommendation can be trusted.
        """
        out: list[DecisionCard] = []

        # Advertising money decisions are produced by _advertising_control_decisions().

        # --- Funnel: enough traffic but weak card→cart conversion ---
        funnel=_rows(_snap(snapshots,'funnel','funnel_7d'))
        for row in funnel:
            sku=str(row.get('nmID') or row.get('nmId') or row.get('nm_id') or '')
            views=_pick_num(row,'openCardCount','views','open_card_count') or 0
            carts=_pick_num(row,'addToCartCount','carts','add_to_cart') or 0
            orders=_pick_num(row,'ordersCount','orders','orders_count') or 0
            conv=row.get('conversions') if isinstance(row.get('conversions'),dict) else {}
            cart_pct=_n(conv.get('addToCartPercent')) or ((carts/views*100) if views else None)
            if views>=500 and cart_pct is not None and cart_pct<7:
                claims=[x for x in _rows(_snap(snapshots,'returns_quality','open_claims')) if str(x.get('nmId') or x.get('nmID') or '')==sku]
                out.append(DecisionCard(
                    decision_key=f'sku:{sku}:weak_card_conversion',scope='sku',entity_id=sku,
                    title=f'nmID {sku}: трафик есть, но карточка слабо переводит в корзину',
                    diagnosis=f"За окно карточку открыли ≈{views:.0f} раз, добавление в корзину ≈{cart_pct:.1f}%, заказов {orders:.0f}. Это больше похоже на проблему оффера/карточки/цены, чем на нехватку показов." + (f" Параллельно открыто возвратных претензий: {len(claims)}." if claims else ''),
                    priority='high',confidence='high' if views>=1000 else 'medium',
                    recommended_actions=[
                        {'step':1,'action':'Не покупать дополнительный трафик, пока не исправлена конверсия карточки','mode':'advertising_guard'},
                        {'step':2,'action':'Сравнить цену, рейтинг, отзывы и первый экран карточки с релевантной выдачей WB','mode':'market_card_analysis'},
                        {'step':3,'action':'Если возвраты повторяют одну причину — сначала исправить описание/комплектацию/качество, затем провести один A/B-тест карточки','mode':'experiment'},
                    ],
                    evidence=[_ev('WB Funnel','card_views',views),_ev('WB Funnel','cart_conversion_pct',round(cart_pct,1)),_ev('WB Funnel','orders',orders),_ev('WB Returns','open_claims_same_sku',len(claims))],
                    follow_up='Оценить новую конверсию после достаточного числа просмотров, не раньше.'
                ))

        # --- Product/content blocking errors ---
        card_errors=_rows(_snap(snapshots,'cards','card_errors'))
        for row in card_errors[:20]:
            sku=str(row.get('nmID') or row.get('nmId') or row.get('id') or '')
            err=str(row.get('error') or row.get('message') or 'Ошибка карточки')
            out.append(DecisionCard(
                decision_key=f'sku:{sku}:card_error',scope='sku',entity_id=sku,
                title=f'nmID {sku}: сначала исправить карточку, потом масштабировать продажи',
                diagnosis=err,
                priority='critical',confidence='high',
                recommended_actions=[
                    {'step':1,'action':'Исправить указанную обязательную характеристику/ошибку карточки','mode':'content_fix'},
                    {'step':2,'action':'До исправления не увеличивать рекламный бюджет этой карточки','mode':'advertising_guard'},
                    {'step':3,'action':'После прохождения проверки WB перепроверить индексацию и поисковую видимость','mode':'follow_up'},
                ],evidence=[_ev('WB Content','card_error',err)],follow_up='Контроль после следующей синхронизации карточек.'
            ))

        # --- Quality incident: returns + chats + deductions become one root-cause plan ---
        claims=_rows(_snap(snapshots,'returns_quality','open_claims'))
        chats=_rows(_snap(snapshots,'buyer_chats','chat_events'))
        deductions=_rows(_snap(snapshots,'cost_guard','deductions'))
        claim_by_sku: dict[str,list[dict[str,Any]]] = {}
        for x in claims:
            sku=str(x.get('nmId') or x.get('nmID') or x.get('sku') or '')
            if sku: claim_by_sku.setdefault(sku,[]).append(x)
        ret_warn=int(self.policy.thresholds.get('returns',{}).get('open_claims_warn',3))
        quality_words=('не тот','не соответствует','комплект','вложен','вложение','перепут')
        chat_quality=[x for x in chats if any(w in str(x.get('message') or x.get('text') or '').lower() for w in quality_words)]
        deduction_quality=[x for x in deductions if any(w in str(x.get('reason') or '').lower() for w in quality_words)]
        for sku,rows in claim_by_sku.items():
            if len(rows)<ret_warn: continue
            reasons=[str(x.get('reason') or '') for x in rows]
            main_reason=max(set(reasons),key=reasons.count) if reasons else 'повторяющиеся возвраты'
            extra=[]
            if chat_quality: extra.append(f"чатов с похожей жалобой: {len(chat_quality)}")
            if deduction_quality: extra.append(f"удержаний/штрафов с похожей причиной: {len(deduction_quality)}")
            out.append(DecisionCard(
                decision_key=f'sku:{sku}:quality_root_cause',scope='sku',entity_id=sku,
                title=f'nmID {sku}: повторяющаяся проблема качества — нужен root-cause, а не больше рекламы',
                diagnosis=f"Открыто {len(rows)} претензии; основная причина: «{main_reason}»." + (" Дополнительные сигналы: "+', '.join(extra)+'.' if extra else ''),
                priority='critical',confidence='high',
                recommended_actions=[
                    {'step':1,'action':'Не масштабировать рекламу SKU до устранения повторяющейся причины','mode':'advertising_guard'},
                    {'step':2,'action':'Сверить карточку и фактическую комплектацию; отдельно проверить сборку/маркировку на ФФ','mode':'quality_audit'},
                    {'step':3,'action':'Выбрать 5–10 последних проблемных заказов и найти общий этап ошибки: контент → комплектация → упаковка → сборка → маркировка','mode':'root_cause_analysis'},
                    {'step':4,'action':'После исправления контролировать долю возвратов/жалоб на следующей когорте заказов','mode':'follow_up'},
                ],
                evidence=[_ev('WB Returns','open_claims',len(rows),main_reason),_ev('Buyer chats','quality_messages',len(chat_quality)),_ev('WB deductions','quality_deductions',len(deduction_quality))],
                follow_up='Закрывать инцидент только после новой когорты без повторения причины.'
            ))

        # Deductions/penalties that are material even without a matching quality incident.
        fincfg=self.policy.thresholds.get('finance',{}); penalty_warn=float(fincfg.get('penalty_alert_rub',1000))
        material=[x for x in deductions if (_pick_num(x,'amount','sum','penalty') or 0)>=penalty_warn]
        if material:
            total=sum((_pick_num(x,'amount','sum','penalty') or 0) for x in material)
            out.append(DecisionCard(
                decision_key='finance:material_deductions',scope='finance',entity_id='deductions',
                title='Есть материальные удержания — их нужно превратить в устранимую операционную причину',
                diagnosis=f"Найдено {len(material)} удержаний выше порога; сумма по текущему срезу ≈{total:.0f} ₽.",
                priority='high',confidence='high',
                recommended_actions=[
                    {'step':1,'action':'Разложить удержания по причине и SKU/заказу, а не списывать одной строкой расходов','mode':'finance_audit'},
                    {'step':2,'action':'Если причина повторяется — создать операционный контроль на ФФ/карточку/маркировку','mode':'root_cause_analysis'},
                    {'step':3,'action':'Проверить возможность претензии/оспаривания только там, где есть доказательства','mode':'claims_review'},
                ],evidence=[_ev('WB deductions','material_total_rub',total),*[_ev('WB deductions',str(x.get('nmID') or x.get('nmId') or 'item'),_pick_num(x,'amount','sum','penalty'),str(x.get('reason') or '')) for x in material[:5]]],
                follow_up='Сравнить сумму удержаний на следующей неделе после исправления причины.'
            ))

        # FBS reshipment is an operational exception that should be resolved explicitly.
        reship=_rows(_snap(snapshots,'orders_fbs','reshipment'))
        if reship:
            ids=[str(x.get('orderId') or x.get('id') or '') for x in reship[:10]]
            out.append(DecisionCard(
                decision_key='fbs:reshipment',scope='fbs',entity_id='reshipment',
                title='Есть FBS-переотгрузки — проверить, чтобы заказ не потерялся и остаток не списался неверно',
                diagnosis=f"Система видит {len(reship)} заказ(а/ов), требующих сценария повторной отгрузки.",
                priority='high',confidence='high',
                recommended_actions=[
                    {'step':1,'action':'Проверить статус каждого заказа и факт наличия товара на ФФ','mode':'fbs_check'},
                    {'step':2,'action':'Сверить, не был ли товар уже списан/зарезервирован первой попыткой','mode':'stock_reconcile'},
                    {'step':3,'action':'После повторной отгрузки убедиться, что заказ исчез из очереди исключений','mode':'follow_up'},
                ],evidence=[_ev('WB FBS','reshipment_orders',', '.join(ids))],follow_up='Повторная проверка на следующем 10-минутном цикле.'
            ))

        # Promotion price must never be accepted without unit-economics floor.
        promos=_rows(_snap(snapshots,'price_margin','promotions'))
        for row in promos[:30]:
            sku=str(row.get('nmID') or row.get('nmId') or '')
            current=_pick_num(row,'price','currentPrice','discountedPrice'); plan=_pick_num(row,'planPrice','promoPrice','priceWithDiscount')
            if sku and plan is not None and current is not None and plan<current:
                own=next((x for x in (p.get('own_27') or {}).get('products',[]) if str(x.get('sku'))==sku),None)
                if own:
                    profit=_n(own.get('profit_rub')); margin=_n(own.get('margin_pct'))
                    diagnosis=f"Акция предлагает цену {plan:.0f} ₽ вместо {current:.0f} ₽. Текущая юнитка: прибыль {profit if profit is not None else '—'} ₽, маржа {margin if margin is not None else '—'}%."
                    blockers=[]
                else:
                    diagnosis=f"Акция предлагает цену {plan:.0f} ₽ вместо {current:.0f} ₽, но подтверждённой юнитки этого SKU в trusted-источнике нет."
                    blockers=['Нет подтверждённой себестоимости/юнитки SKU — участие в акции нельзя рекомендовать.']
                out.append(DecisionCard(
                    decision_key=f'sku:{sku}:promotion_guard',scope='sku',entity_id=sku,
                    title=f'nmID {sku}: акция снижает цену — сначала пересчитать прибыль',diagnosis=diagnosis,
                    priority='high',confidence='high' if own else 'medium',
                    recommended_actions=[
                        {'step':1,'action':'Не входить в акцию автоматически','mode':'price_guard'},
                        {'step':2,'action':'Пересчитать прибыль/маржу/ROI на акционной цене с комиссией, логистикой, налогом и рекламой','mode':'unit_economics'},
                        {'step':3,'action':'Рекомендовать участие только если абсолютная прибыль и минимальная маржа остаются допустимыми','mode':'read_only_recommendation'},
                    ],evidence=[_ev('WB Promotions','current_price',current),_ev('WB Promotions','promo_price',plan),_ev('27/Юнитка','profit_rub',own.get('profit_rub') if own else None)],blockers=blockers,
                    follow_up='Пересчитать после изменения условий акции или юнитки.'
                ))

        # Supply acceptance: choose the economically safer available warehouse, but do not ignore localisation.
        acceptance=_rows(_snap(snapshots,'supply','acceptance'))
        available=[x for x in acceptance if x.get('allowUnload') is not False and _pick_num(x,'coefficient') is not None]
        if len(available)>=2:
            best=min(available,key=lambda x:_pick_num(x,'coefficient') or 0); worst=max(available,key=lambda x:_pick_num(x,'coefficient') or 0)
            bc=_pick_num(best,'coefficient') or 0; wc=_pick_num(worst,'coefficient') or 0
            if wc>bc:
                out.append(DecisionCard(
                    decision_key='supply:acceptance_choice',scope='supply',entity_id='warehouses',
                    title='Есть разница в коэффициентах приёмки — учитывать её в плане поставки',
                    diagnosis=f"{best.get('warehouseName') or best.get('warehouse_name')}: коэффициент {bc:g}; {worst.get('warehouseName') or worst.get('warehouse_name')}: {wc:g}. Но дешёвая приёмка не должна ухудшать локализацию/скорость доставки.",
                    priority='medium',confidence='high',
                    recommended_actions=[
                        {'step':1,'action':f"Использовать {best.get('warehouseName') or best.get('warehouse_name')} как базовый дешёвый вариант приёмки","mode":"supply_candidate"},
                        {'step':2,'action':'Перед переносом объёма сверить региональный спрос, локализацию и стоимость последней мили','mode':'localisation_guard'},
                        {'step':3,'action':'Разделить поставку, если более дорогой склад нужен для продаж в своём регионе','mode':'supply_plan'},
                    ],evidence=[_ev('WB Acceptance',str(best.get('warehouseName') or 'best'),'coefficient='+str(bc)),_ev('WB Acceptance',str(worst.get('warehouseName') or 'worst'),'coefficient='+str(wc))],
                    follow_up='Проверять коэффициенты перед каждой новой поставкой.'
                ))
        return out

    def _event_decisions(self, p: dict[str, Any], snapshots: dict[str, Any]) -> list[DecisionCard]:
        """Translate detector-only events that need historical context into action plans."""
        out: list[DecisionCard] = []
        events=snapshots.get('_events') or []
        if not isinstance(events,list):
            return out
        for e in events:
            if not isinstance(e,dict): continue
            agent=str(e.get('agent') or ''); key=str(e.get('key') or ''); severity=str(e.get('severity') or 'info')
            payload=e.get('payload') if isinstance(e.get('payload'),dict) else {}
            if agent=='search_positions' and key.startswith('position:') and severity in {'warning','critical'}:
                after=payload.get('after') if isinstance(payload.get('after'),dict) else {}
                before=payload.get('before') if isinstance(payload.get('before'),dict) else {}
                sku=str(after.get('nm_id') or before.get('nm_id') or key.split(':',1)[-1])
                query=str(after.get('query') or before.get('query') or '')
                old=_n(before.get('position')); now=_n(after.get('position'))
                out.append(DecisionCard(
                    decision_key=f'sku:{sku}:position_drop:{query}',scope='sku',entity_id=sku,
                    title=f'nmID {sku}: позиция просела — сначала найти причину, а не повышать ставку',
                    diagnosis=(f"По запросу «{query}» позиция изменилась {old:.0f} → {now:.0f}. " if old is not None and now is not None else f"По запросу «{query}» зафиксирована просадка позиции. ")+'Одной рекламной ставкой это объяснять нельзя: причина может быть в остатке, цене, CR, карточке или рынке.',
                    priority='critical' if severity=='critical' else 'high',confidence='high',
                    recommended_actions=[
                        {'step':1,'action':'Зафиксировать ставку и не повышать её автоматически','mode':'advertising_guard'},
                        {'step':2,'action':'Сверить остаток/дни покрытия, цену, юнитку, CR карточки и публичную выдачу конкурентов','mode':'cross_contour_analysis'},
                        {'step':3,'action':'После определения причины менять один фактор и измерять позицию + прибыль, а не только место в выдаче','mode':'experiment'},
                    ],
                    evidence=[_ev('WB Search Analytics','query',query),_ev('WB Search Analytics','position_before',old),_ev('WB Search Analytics','position_after',now)],
                    follow_up='Повторный замер позиции после одного полного окна выдачи/рекламы.'
                ))
            elif agent=='reviews_questions' and key in {'unanswered_feedbacks','unanswered_questions'} and severity in {'warning','critical'}:
                kind='отзывы' if 'feedback' in key else 'вопросы'
                out.append(DecisionCard(
                    decision_key=f'customer:{key}',scope='customer',entity_id=kind,
                    title=f'Накопились неотвеченные {kind} — это уже операционная задача',
                    diagnosis=str(e.get('message') or e.get('title') or ''),priority='high',confidence='high',
                    recommended_actions=[
                        {'step':1,'action':f'Разобрать новые {kind}: сначала негатив/проблемы товара, затем обычные обращения','mode':'customer_triage'},
                        {'step':2,'action':'Повторяющиеся жалобы связать с возвратами и карточкой соответствующего SKU','mode':'quality_link'},
                        {'step':3,'action':'После ответа проверить, нет ли одной причины, требующей изменения товара/контента/ФФ','mode':'root_cause_analysis'},
                    ],evidence=[_ev('WB Feedbacks',key,e.get('message'))],follow_up='Перепроверить очередь на следующем 15-минутном цикле.'
                ))
            elif agent=='api_health' and key in {'wb_api_degradation','token_info_failed','degradations_check_failed'} and severity in {'warning','critical'}:
                out.append(DecisionCard(
                    decision_key=f'system:{key}',scope='system',entity_id='wb_api',
                    title='Источник WB API нестабилен — денежные выводы нужно временно ограничить',
                    diagnosis=str(e.get('message') or e.get('title') or ''),priority='high',confidence='high',
                    recommended_actions=[
                        {'step':1,'action':'Не принимать новые денежные решения на неполных/устаревших Seller API данных','mode':'data_guard'},
                        {'step':2,'action':'Продолжить мониторинг по trusted Sheets и публичным read-only источникам, где это допустимо','mode':'fallback'},
                        {'step':3,'action':'Вернуть Seller API в decision graph только после успешного health-check','mode':'recovery'},
                    ],evidence=[_ev('API Health',key,e.get('message'))],follow_up='Автоматический health-check каждые 10 минут.'
                ))
        return out

    def _search_market_decisions(self, p: dict[str, Any], snapshots: dict[str, Any]) -> list[DecisionCard]:
        out=[]
        market=snapshots.get('competitors',{})
        pos=snapshots.get('search_positions',{}).get('positions',{}).get('data',{}) if isinstance(snapshots.get('search_positions'),dict) else {}
        # Competitor agent stores one snapshot per query; API dashboard passes them when available.
        for query, snap in market.items() if isinstance(market,dict) else []:
            data=snap.get('data',{}) if isinstance(snap,dict) else {}
            analysis=data.get('analysis') or {}
            if analysis.get('own_position') and analysis.get('own_position')>20 and analysis.get('price_vs_median_pct') is not None:
                delta=float(analysis['price_vs_median_pct'])
                rating_delta=_n(analysis.get('rating_vs_median'))
                if delta>10:
                    action='Проверить цену и оффер относительно релевантных конкурентов'
                elif rating_delta is not None and rating_delta < -0.15:
                    action='Сначала разобрать рейтинг/отзывы и карточку: цена не выглядит главной причиной слабой выдачи'
                else:
                    action='Проверить CTR/карточку/релевантность запроса; цена не выглядит главной причиной'
                out.append(DecisionCard(
                    decision_key=f"market:{query}:visibility", scope='market', entity_id=query,
                    title=f"По запросу «{query}» видимость слабая — есть рыночный контекст",
                    diagnosis=f"Позиция собственного товара около {analysis['own_position']}; отклонение цены от медианы релевантной выдачи {delta:+.1f}%.",
                    priority='high', confidence='medium',
                    recommended_actions=[{"step":1,"action":action,"mode":"analysis"},{"step":2,"action":"Не повышать ставку, пока не исключены цена/карточка/остаток как причина","mode":"advertising_guard"},{"step":3,"action":"После изменения измерить позицию, CTR, CR и прибыль","mode":"experiment"}],
                    evidence=[_ev('WB public search','own_position',analysis.get('own_position')),_ev('WB public search','price_vs_median_pct',delta),_ev('WB public search','own_rating',analysis.get('own_rating')),_ev('WB public search','rating_vs_median',analysis.get('rating_vs_median')),_ev('WB public search','competitors',analysis.get('competitor_count'))],
                    follow_up='Повторить рыночный срез через 2–6 часов для быстрых запросов или на следующий день для медленных.'
                ))
        return out
