from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any

PROMO_PRICE_KEYS = (
    "priceAfterPromotion", "price_after_promotion", "priceAfterPromo",
    "price_after_promo", "promoPrice", "promo_price", "priceWithPromotion",
    "price_with_promotion", "specialPrice", "special_price",
)
PROMO_LABEL_KEYS = ("name", "actionName", "action_name", "promoName", "promo_name", "title")
VALID_METRICS = {"ctr", "cart_from_click", "order_from_click", "orders_per_view"}
STORE_CODES = {"ap", "aa", "yv"}


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(str(value).replace(",", ".").replace(" ", ""))
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _record_list(value: Any, depth: int = 0) -> list[dict[str, Any]]:
    if depth > 6:
        return []
    if isinstance(value, list):
        rows = [item for item in value if isinstance(item, dict)]
        if rows:
            return rows
        return [row for item in value for row in _record_list(item, depth + 1)]
    if not isinstance(value, dict):
        return []
    for key in ("items", "data", "result", "response", "promotions", "actions",
                "goods", "products", "feedbacks", "questions", "events", "chats"):
        nested = value.get(key)
        if isinstance(nested, (list, dict)):
            rows = _record_list(nested, depth + 1)
            if rows:
                return rows
    rows: list[dict[str, Any]] = []
    for nested in value.values():
        if isinstance(nested, (list, dict)):
            rows.extend(_record_list(nested, depth + 1))
    return rows


def _first(value: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in value and value[key] is not None and value[key] != "":
            return value[key]
    for child in value.values():
        if isinstance(child, dict):
            found = _first(child, keys)
            if found is not None:
                return found
    return None


def _nm_id(value: dict[str, Any]) -> str | None:
    raw = _first(value, ("nmID", "nmId", "nm_id", "sku", "wildberriesArticle"))
    if raw is None or not str(raw).strip().isdigit():
        return None
    return str(raw).strip()


def _shop_code(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    aliases = {
        "ap": "ap", "sanych": "ap", "саныч": "ap",
        "aa": "aa", "air": "aa",
        "yv": "yv", "hozyushka": "yv", "хозяюшка": "yv",
    }
    return aliases.get(text)


def _snapshot_data(snapshots: dict[str, Any], source: str, key: str) -> tuple[Any, str | None]:
    row = (snapshots.get(source) or {}).get(key)
    if not isinstance(row, dict):
        return None, None
    return row.get("data"), row.get("created_at")


def _products(portfolio: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    found: dict[tuple[str, str], dict[str, Any]] = {}
    candidates: list[tuple[str | None, dict[str, Any]]] = []

    def add_rows(value: Any, inherited_shop: str | None = None) -> None:
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    candidates.append((inherited_shop, item))
        elif isinstance(value, dict):
            for item in value.values():
                if isinstance(item, list):
                    add_rows(item, inherited_shop)
                elif isinstance(item, dict):
                    if _nm_id(item):
                        candidates.append((inherited_shop, item))
                    else:
                        add_rows(item, inherited_shop)

    add_rows(portfolio.get("products"))
    own = portfolio.get("own_27")
    if isinstance(own, dict):
        add_rows(own.get("products"))
        crosswalk = own.get("unit_economics_by_shop_nm_id")
        if isinstance(crosswalk, dict):
            for shop, rows in crosswalk.items():
                add_rows(list(rows.values()) if isinstance(rows, dict) else rows, _shop_code(shop))

    for inherited_shop, item in candidates:
        nm = _nm_id(item)
        shop = _shop_code(item.get("shop_code") or item.get("cabinet") or item.get("store_id")) or inherited_shop
        if nm and shop in STORE_CODES:
            found[(shop, nm)] = item
    return found


def _promo_rows(value: Any) -> list[dict[str, Any]]:
    rows = _record_list(value)
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for row in rows:
        nm = _nm_id(row)
        if not nm:
            continue
        label = _first(row, PROMO_LABEL_KEYS)
        price = _first(row, PROMO_PRICE_KEYS)
        promo_id = _first(row, ("id", "promoID", "promoId", "promo_id", "actionId", "action_id"))
        if label is None and price is None and promo_id is None:
            continue
        marker = (nm, str(promo_id or ""), str(label or ""))
        if marker in seen:
            continue
        seen.add(marker)
        out.append(row)
    return out


def _product_for_promo(row: dict[str, Any], products: dict[tuple[str, str], dict[str, Any]]) -> tuple[str | None, dict[str, Any] | None]:
    nm = _nm_id(row)
    if not nm:
        return None, None
    shop = _shop_code(_first(row, ("shop_code", "cabinet", "store", "store_name", "shop")))
    if shop:
        return shop, products.get((shop, nm))
    matches = [(code, product) for (code, product_nm), product in products.items() if product_nm == nm]
    return matches[0] if len(matches) == 1 else (None, None)


def _profit_scenario(product: dict[str, Any], promo_price: float) -> tuple[float | None, str | None, str | None]:
    base_price = _number(product.get("price_rub") or product.get("price_after_discount_rub"))
    cost = _number(product.get("cost_rub"))
    recorded_profit = _number(product.get("profit_rub") or product.get("plan_profit_rub"))
    logistics = _number(product.get("logistics_total_rub"))
    acceptance = _number(product.get("acceptance_rub"))
    commission = _number(product.get("commission_pct"))
    acquiring = _number(product.get("acquiring_pct"))
    constructor = _number(product.get("constructor_pct"))
    tax = _number(product.get("tax_total_rub"))
    plan_drr = _number(product.get("plan_drr_pct") or product.get("drr_pct"))

    required = {
        "цена из юнит-экономики": base_price,
        "себестоимость": cost,
        "прибыль в юнит-экономике": recorded_profit,
        "логистика": logistics,
        "комиссия": commission,
        "налог": tax,
    }
    missing = [label for label, value in required.items() if value is None]
    if missing:
        return None, None, "Не хватает точных полей: " + ", ".join(missing)
    if base_price <= 0 or cost < 0 or commission < 0 or commission >= 100:
        return None, None, "Проверка исходной цены, себестоимости или комиссии не пройдена."

    fixed = logistics + (acceptance or 0.0)
    tax_rate = tax / base_price * 100.0
    variable_rate = commission + (acquiring or 0.0) + (constructor or 0.0) + (plan_drr or 0.0) + tax_rate
    baseline = base_price * (1.0 - variable_rate / 100.0) - cost - fixed
    tolerance = max(5.0, abs(recorded_profit) * 0.05)
    if abs(baseline - recorded_profit) > tolerance:
        return None, None, (
            "Состав расходов не сходится с прибылью в юнит-экономике "
            f"(расчёт {baseline:.2f} ₽, источник {recorded_profit:.2f} ₽)."
        )
    if promo_price <= 0:
        return None, None, "Цена участия в акции должна быть положительной."
    profit = baseline + (promo_price - base_price) * (1.0 - variable_rate / 100.0)

    target_profit = _number(product.get("target_profit_rub"))
    target_margin = _number(product.get("target_margin_pct"))
    if target_profit is None and target_margin is None:
        return profit, None, None
    floor = max(target_profit or 0.0, promo_price * (target_margin or 0.0) / 100.0)
    return profit, floor, None


def _promotion_recommendations(snapshots: dict[str, Any], portfolio: dict[str, Any]) -> dict[str, Any]:
    data, observed_at = _snapshot_data(snapshots, "price_margin", "promotions")
    products = _products(portfolio)
    promos = _promo_rows(data) if data is not None else []
    rows: list[dict[str, Any]] = []
    for promo in promos:
        nm = _nm_id(promo)
        shop, product = _product_for_promo(promo, products)
        promo_price = _number(_first(promo, PROMO_PRICE_KEYS))
        label = str(_first(promo, PROMO_LABEL_KEYS) or _first(promo, ("id", "promoID", "promoId")) or "Акция WB")
        item: dict[str, Any] = {
            "nm_id": nm, "shop_code": shop, "product_name": (
                product.get("name") or product.get("product_name") or product.get("seller_article")
                if product else None
            ),
            "promotion": label, "promo_price_rub": promo_price, "observed_at": observed_at,
            "recommendation": "blocked", "reason": None,
            "estimated_profit_rub": None, "target_profit_rub": None,
        }
        if product is None:
            item["reason"] = "Нет единственной точной связи акции с товаром и магазином."
        elif not bool(product.get("unit_economics_decision_ready", False)):
            item["reason"] = "Юнит-экономика товара не подтверждена для денежного решения."
        elif promo_price is None:
            item["reason"] = "WB не передал точную цену участия в этой акции."
        else:
            profit, floor, error = _profit_scenario(product, promo_price)
            item["estimated_profit_rub"] = round(profit, 2) if profit is not None else None
            item["target_profit_rub"] = round(floor, 2) if floor is not None else None
            if error:
                item["reason"] = error
            elif floor is None:
                item["recommendation"] = "needs_target"
                item["reason"] = "Оценка прибыли рассчитана, но целевой уровень не задан; решение о входе не выдано."
            elif profit is not None and profit >= floor:
                item["recommendation"] = "consider"
                item["reason"] = "Расчёт выше целевой прибыли/маржи; дополнительный трафик акции не гарантирован."
            else:
                item["recommendation"] = "decline"
                item["reason"] = "Цена акции опускает расчёт ниже целевой прибыли/маржи."
        rows.append(item)
    return {
        "status": "available" if data is not None else "missing",
        "observed_at": observed_at,
        "row_count": len(rows),
        "rows": rows,
        "automatic_entry": False,
        "note": "Решение только рекомендательное. Вступление в акцию не выполняется.",
    }


def _review_candidates(snapshots: dict[str, Any]) -> dict[str, Any]:
    answered, observed_at = _snapshot_data(snapshots, "reviews_questions", "answered_feedbacks")
    if answered is None:
        return {
            "status": "missing", "observed_at": observed_at, "rows": [],
            "note": "Снимок отвеченных отзывов не получен. Это не означает, что кандидатов нет.",
        }
    if isinstance(answered, dict) and answered.get("source_status") == "unavailable":
        return {
            "status": "unavailable", "observed_at": observed_at, "rows": [],
            "note": str(answered.get("reason") or "Источник отвеченных отзывов недоступен."),
        }
    rows = _record_list(answered)
    candidates = []
    for row in rows:
        rating = _number(_first(row, ("productValuation", "valuation", "rating", "stars")))
        if rating is None or rating < 4:
            continue
        text = str(_first(row, ("text", "textReview", "pros", "cons", "comment")) or "").strip()
        if len(text) < 20:
            continue
        pinned_raw = _first(row, ("isPinned", "is_pinned", "pinned", "pinStatus", "pin_status"))
        pinned_text = str(pinned_raw or "").strip().casefold()
        is_pinned = pinned_raw is True or pinned_text in {"true", "1", "да", "pinned", "закреплен", "закреплено"}
        if is_pinned:
            continue
        details = _first(row, ("productDetails", "product_details"))
        details = details if isinstance(details, dict) else {}
        nm = _nm_id(details) or _nm_id(row)
        photos = _first(row, ("photoLinks", "photos", "photo_links"))
        has_media = bool(photos) or bool(_first(row, ("video", "videoLinks", "video_links")))
        score = int(round(rating * 10 + min(20, len(text) // 10) + (10 if has_media else 0)))
        candidates.append({
            "id": str(_first(row, ("id", "feedbackId", "feedback_id")) or ""),
            "nm_id": nm,
            "shop_code": _shop_code(_first(row, ("shop_code", "cabinet", "store"))),
            "rating": rating,
            "text": text[:700],
            "score": score,
            "has_media": has_media,
            "pin_status": "unknown" if pinned_raw is None else "not_pinned",
            "observed_at": observed_at,
            "reason": "Отвеченный отзыв с высокой оценкой и содержательным текстом" + ("; есть фото/видео" if has_media else ""),
        })
    candidates.sort(key=lambda item: (item["score"], item["rating"]), reverse=True)
    return {
        "status": "available" if rows else "empty",
        "observed_at": observed_at,
        "rows": candidates[:30],
        "note": "Кандидаты для ручной проверки. Если источник не передал состояние закрепления, оно показано как неизвестное.",
    }


def _chat_classification(text: str) -> tuple[str, str, str | None]:
    value = text.casefold()
    legal = ("правооблад", "товарный знак", "суд", "юрист", "претензи", "иск")
    complaint = ("брак", "слом", "повреж", "жалоб", "не работает", "возврат", "не подош")
    delivery = ("доставка", "заказ", "посыл", "где товар", "когда привез", "срок")
    if any(token in value for token in legal):
        return "юридический риск · передать человеку", "critical", None
    if any(token in value for token in complaint):
        return (
            "проблема товара · проверить человеку", "high",
            "Здравствуйте! Спасибо, что сообщили. Мы проверим ситуацию по вашему обращению и вернёмся с подтверждённой информацией.",
        )
    if any(token in value for token in delivery):
        return (
            "доставка и заказ · проверить статус", "normal",
            "Здравствуйте! Проверим статус заказа и вернёмся с подтверждённой информацией.",
        )
    if text.strip():
        return (
            "обычный вопрос · требуется проверка", "normal",
            "Здравствуйте! Спасибо за вопрос. Проверим информацию и ответим здесь.",
        )
    return "нет текста · нужен полный контекст", "unknown", None


def _chat_queue(snapshots: dict[str, Any]) -> dict[str, Any]:
    data, observed_at = _snapshot_data(snapshots, "buyer_chats", "chat_events")
    rows = _record_list(data) if data is not None else []
    output = []
    for row in rows[:100]:
        text = _first(row, ("text", "message", "messageText", "lastMessage", "content"))
        if isinstance(text, dict):
            text = _first(text, ("text", "content"))
        message = str(text or "").strip()[:700]
        category, priority, draft = _chat_classification(message)
        output.append({
            "chat_id": str(_first(row, ("chatID", "chatId", "chat_id", "id")) or ""),
            "nm_id": _nm_id(row),
            "shop_code": _shop_code(_first(row, ("shop_code", "cabinet", "store"))),
            "text": message,
            "category": category,
            "priority": priority,
            "classification_basis": "Предварительная сортировка по словам; проверь перед использованием.",
            "draft": draft,
            "observed_at": observed_at,
            "reply_sent": False,
            "state": "черновик не отправлен" if draft else "требуется полный контекст",
        })
    return {
        "status": "available" if data is not None else "missing",
        "observed_at": observed_at,
        "rows": output,
        "automatic_replies": False,
        "note": "Черновики формируются по простым правилам и не отправляются. Юридические и спорные случаи требуют человека.",
    }

def product_choices(portfolio: dict[str, Any]) -> list[dict[str, Any]]:
    choices = []
    for (shop, nm), product in sorted(_products(portfolio).items()):
        choices.append({
            "shop_code": shop, "nm_id": nm,
            "name": str(product.get("name") or product.get("product_name") or product.get("seller_article") or nm),
            "unit_economics_decision_ready": bool(product.get("unit_economics_decision_ready", False)),
        })
    return choices


def known_product_ids(portfolio: dict[str, Any], snapshots: dict[str, Any]) -> set[tuple[str, str]]:
    known = set(_products(portfolio))
    cards, _ = _snapshot_data(snapshots, "cards", "card_catalog")
    for row in _record_list(cards) if cards is not None else []:
        nm = _nm_id(row)
        shop = _shop_code(_first(row, ("shop_code", "cabinet", "store")))
        if nm and shop:
            known.add((shop, nm))
    return known


def _card_catalog(snapshots: dict[str, Any], portfolio: dict[str, Any]) -> dict[str, Any]:
    data, observed_at = _snapshot_data(snapshots, "cards", "card_catalog")
    if data is None:
        return {"status": "missing", "observed_at": None, "complete": None, "rows": [], "note": "Снимок карточек WB отсутствует."}
    raw_cards = data.get("cards") if isinstance(data, dict) else None
    cards = [x for x in raw_cards if isinstance(x, dict)] if isinstance(raw_cards, list) else _record_list(data)
    products = _products(portfolio)
    by_nm: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for (shop, nm), product in products.items():
        by_nm.setdefault(nm, []).append((shop, product))
    output = []
    for card in cards[:5000]:
        nm = _nm_id(card)
        if not nm:
            continue
        explicit_shop = _shop_code(_first(card, ("shop_code", "cabinet", "store", "shop")))
        matches = by_nm.get(nm, [])
        shop = explicit_shop or (matches[0][0] if len(matches) == 1 else None)
        dimensions = card.get("dimensions") if isinstance(card.get("dimensions"), dict) else {}
        sizes = card.get("sizes") if isinstance(card.get("sizes"), list) else None
        photos = card.get("photos") if isinstance(card.get("photos"), list) else None
        characteristics = card.get("characteristics") if isinstance(card.get("characteristics"), list) else None
        barcodes = []
        if sizes is not None:
            for size in sizes:
                if not isinstance(size, dict):
                    continue
                sku_values = size.get("skus")
                if isinstance(sku_values, list):
                    barcodes.extend(str(x) for x in sku_values if x)
        direct_barcodes = card.get("barcodes")
        if not barcodes and isinstance(direct_barcodes, list):
            barcodes = [str(x) for x in direct_barcodes if x]
        product = products.get((shop, nm)) if shop else None
        output.append({
            "nm_id": nm,
            "shop_code": shop,
            "product_name": str(
                _first(card, ("title", "imt_name", "subjectName", "subject", "vendorCode", "supplierArticle"))
                or (product.get("name") if product else "")
                or nm
            ),
            "vendor_code": str(_first(card, ("vendorCode", "supplierArticle", "vendor_code")) or ""),
            "photo_count": len(photos) if photos is not None else None,
            "size_count": len(sizes) if sizes is not None else None,
            "barcode_count": len(barcodes) if sizes is not None or isinstance(direct_barcodes, list) else None,
            "characteristic_count": len(characteristics) if characteristics is not None else None,
            "dimensions": {
                key: dimensions.get(key) for key in ("length", "width", "height", "weightBrutto")
                if key in dimensions
            },
            "field_visibility": {
                "photos": "present" if photos else ("empty" if photos == [] else "not_returned"),
                "sizes": "present" if sizes else ("empty" if sizes == [] else "not_returned"),
                "characteristics": "present" if characteristics else ("empty" if characteristics == [] else "not_returned"),
                "dimensions": "present" if dimensions else "not_returned",
            },
        })
    complete = data.get("complete") if isinstance(data, dict) and isinstance(data.get("complete"), bool) else None
    return {
        "status": "available" if cards else "empty",
        "observed_at": observed_at,
        "count": len(cards),
        "complete": complete,
        "rows": output,
        "note": (
            "Показаны только поля из снимка каталога. Пустое значение отмечается как пустое лишь когда это явно передал источник; отсутствие поля не считается ошибкой карточки."
            if complete is not False else
            "Обход каталога помечен источником как неполный; строки нельзя считать полным списком товаров."
        ),
    }


def _validated_photo_metadata(value: Any, *, min_height: int) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("Выбери файл нового фото для проверки требований WB.")
    name = str(value.get("name") or "").strip()
    mime = str(value.get("mime") or "").strip().lower()
    width = _number(value.get("width"))
    height = _number(value.get("height"))
    size_bytes = _number(value.get("size_bytes"))
    if not name or len(name) > 160:
        raise ValueError("У файла фото должно быть название до 160 символов.")
    if mime not in {"image/jpeg", "image/png", "image/webp"}:
        raise ValueError("Формат фото должен быть JPG, PNG или WEBP.")
    if any(number is None or int(number) != number for number in (width, height, size_bytes)):
        raise ValueError("Не удалось прочитать размер файла или разрешение фото.")
    if width < 700 or height < min_height:
        raise ValueError(f"Разрешение должно быть не меньше 700 × {min_height} пикселей.")
    if size_bytes <= 0 or size_bytes > 10 * 1024 * 1024:
        raise ValueError("Размер фото должен быть не больше 10 МБ.")
    return {
        "name": name,
        "mime": mime,
        "width": int(width),
        "height": int(height),
        "size_bytes": int(size_bytes),
        "checked_in_browser": True,
        "image_uploaded_to_server": False,
    }


def validate_experiment_payload(
    payload: dict[str, Any], *, experiment_id: str, created_at: str
) -> dict[str, Any]:
    title = str(payload.get("title") or "").strip()
    nm = str(payload.get("nm_id") or "").strip()
    shop = _shop_code(payload.get("shop_code"))
    kind = str(payload.get("experiment_type") or "").strip()
    if not title or len(title) > 120:
        raise ValueError("Название должно содержать от 1 до 120 символов.")
    if not nm.isdigit() or len(nm) < 6 or len(nm) > 14:
        raise ValueError("Укажи точный цифровой артикул WB.")
    if shop not in STORE_CODES:
        raise ValueError("Выбери один из подключённых магазинов.")
    if kind not in {"ab_photo_test", "audience_photo"}:
        raise ValueError("Выбери А/Б-тест главного фото или фото для целевой аудитории.")

    common = {
        "id": experiment_id,
        "created_at": created_at,
        "updated_at": created_at,
        "shop_code": shop,
        "nm_id": nm,
        "title": title,
        "experiment_type": kind,
        "status": "planned",
        "results": None,
        "evaluation": None,
        "execution_mode": "manual_in_wb",
        "write_to_wb": False,
    }
    if kind == "ab_photo_test":
        variants_in = payload.get("variants")
        if not isinstance(variants_in, list) or len(variants_in) != 2:
            raise ValueError("А/Б-тест главного фото сравнивает ровно два варианта: текущий и новый.")
        variants = []
        names = set()
        for index, item in enumerate(variants_in, start=1):
            if not isinstance(item, dict):
                raise ValueError("Укажи названия двух вариантов.")
            name = str(item.get("name") or "").strip()
            if not name or len(name) > 60 or name.casefold() in names:
                raise ValueError("Названия двух вариантов должны быть разными и содержать до 60 символов.")
            names.add(name.casefold())
            variants.append({"name": name, "role": "current" if index == 1 else "alternative"})
        duration = _number(payload.get("duration_days"))
        if duration is None or int(duration) != duration or not 7 <= duration <= 60:
            raise ValueError("Для А/Б-теста укажи срок от 7 до 60 дней.")
        metric = str(payload.get("metric") or "ctr").strip()
        if metric not in VALID_METRICS:
            raise ValueError("Неизвестный показатель сравнения.")
        photo_validation = _validated_photo_metadata(payload.get("photo_validation"), min_height=900)
        return {
            **common,
            "photo_validation": photo_validation,
            "duration_days": int(duration),
            "metric": metric,
            "variants": variants,
            "audience": {},
            "photo_name": None,
            "guidance": "WB делит аудиторию примерно пополам. Не меняй главное фото во время теста и проверь остатки. Запуск и модерация выполняются в кабинете WB.",
        }

    audience_in = payload.get("audience") if isinstance(payload.get("audience"), dict) else {}
    audience: dict[str, str] = {}
    for key, label, limit in (
        ("gender", "пол", 30), ("age", "возраст", 30),
        ("family_status", "семейное положение", 60), ("region", "регион", 80),
    ):
        value = str(audience_in.get(key) or "").strip()
        if not value:
            raise ValueError(f"Заполни поле аудитории «{label}».")
        if len(value) > limit:
            raise ValueError(f"Слишком длинное значение поля «{label}».")
        audience[key] = value
    photo_name = str(payload.get("photo_name") or "").strip()
    if not photo_name or len(photo_name) > 120:
        raise ValueError("Укажи название или описание дополнительного фото.")
    return {
        **common,
        "photo_validation": _validated_photo_metadata(payload.get("photo_validation"), min_height=933),
        "duration_days": None,
        "metric": "ctr",
        "variants": [{"name": photo_name, "role": "audience_photo"}],
        "audience": audience,
        "photo_name": photo_name,
        "guidance": "Это не А/Б-тест: дополнительное фото показывается выбранному сегменту. Все четыре поля аудитории обязательны. Публикация проходит модерацию в кабинете WB.",
    }


def evaluate_experiment_results(experiment: dict[str, Any], payload: Any) -> dict[str, Any]:
    kind = str(experiment.get("experiment_type") or "")
    if kind == "audience_photo":
        if not isinstance(payload, dict):
            raise ValueError("Передай показатели фото для целевой аудитории.")
        counts: dict[str, int] = {}
        for key in ("impressions", "clicks", "carts", "orders"):
            raw = payload.get(key, 0)
            number = _number(raw)
            if number is None or number < 0 or int(number) != number or number > 1_000_000_000:
                raise ValueError(f"Показатель «{key}» должен быть целым числом от 0 до 1 000 000 000.")
            counts[key] = int(number)
        rates = {
            "clicks_from_impressions_pct": counts["clicks"] / counts["impressions"] * 100 if counts["impressions"] else None,
            "carts_from_clicks_pct": counts["carts"] / counts["clicks"] * 100 if counts["clicks"] else None,
            "orders_from_carts_pct": counts["orders"] / counts["carts"] * 100 if counts["carts"] else None,
        }
        if counts["clicks"] < 100:
            sample = "insufficient"
            sample_note = "Меньше 100 переходов: данных недостаточно для вывода."
        elif counts["clicks"] < 200 or counts["impressions"] < 3000:
            sample = "preliminary"
            sample_note = "100–199 переходов или менее 3 000 показов: только предварительная гипотеза."
        else:
            sample = "usable_but_not_significance"
            sample_note = "Набрано от 200 переходов и 3 000 показов; дождись 5 000 показов для более устойчивой оценки. Это не проверка статистической значимости."
        return {
            "experiment_type": kind,
            "counts": counts,
            "rates_pct": {key: round(value, 4) if value is not None else None for key, value in rates.items()},
            "sample_status": sample,
            "sample_note": sample_note,
            "statistical_significance_checked": False,
            "winner": None,
        }

    if kind != "ab_photo_test" or not isinstance(payload, list):
        raise ValueError("Передай результаты по двум вариантам А/Б-теста.")
    expected = {str(item.get("name")) for item in experiment.get("variants", []) if isinstance(item, dict)}
    by_name: dict[str, dict[str, int]] = {}
    for row in payload:
        if not isinstance(row, dict):
            raise ValueError("Строка результата должна быть объектом.")
        name = str(row.get("name") or "")
        if name not in expected or name in by_name:
            raise ValueError("Название варианта отсутствует в тесте или повторяется.")
        counts = {}
        for key in ("impressions", "clicks", "carts", "orders"):
            number = _number(row.get(key, 0))
            if number is None or number < 0 or int(number) != number or number > 1_000_000_000:
                raise ValueError(f"Показатель «{key}» должен быть целым числом от 0 до 1 000 000 000.")
            counts[key] = int(number)
        by_name[name] = counts
    if set(by_name) != expected:
        raise ValueError("Нужны результаты по обоим вариантам теста.")

    metric = str(experiment.get("metric") or "ctr")
    variants = []
    rates: dict[str, float | None] = {}
    for name in sorted(expected):
        counts = by_name[name]
        if metric == "ctr":
            numerator, denominator = counts["clicks"], counts["impressions"]
        elif metric == "cart_from_click":
            numerator, denominator = counts["carts"], counts["clicks"]
        elif metric == "order_from_click":
            numerator, denominator = counts["orders"], counts["clicks"]
        else:
            numerator, denominator = counts["orders"], counts["impressions"]
        rate = numerator / denominator * 100 if denominator else None
        rates[name] = rate
        variants.append({"name": name, **counts, "metric_pct": round(rate, 4) if rate is not None else None})
    available = {name: rate for name, rate in rates.items() if rate is not None}
    leader = max(available, key=available.get) if available else None
    spread = max(available.values()) - min(available.values()) if len(available) > 1 else None
    return {
        "experiment_type": kind,
        "metric": metric,
        "variants": variants,
        "descriptive_leader": leader,
        "difference_percentage_points": round(spread, 4) if spread is not None else None,
        "statistical_significance_checked": False,
        "winner": None,
        "warning": "Сравнение описательное. Считать победителя без результата проверки значимости WB нельзя.",
    }


def build_video_workbench(
    snapshots: dict[str, Any], portfolio: dict[str, Any], experiments: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    generated_at = datetime.now(timezone.utc).isoformat()
    chats = _chat_queue(snapshots)
    reviews = _review_candidates(snapshots)
    promotions = _promotion_recommendations(snapshots, portfolio)
    cards = _card_catalog(snapshots, portfolio)
    return {
        "generated_at": generated_at,
        "products": product_choices(portfolio),
        "card_catalog": cards,
        "promotions": promotions,
        "reviews": reviews,
        "chats": chats,
        "experiments": experiments if isinstance(experiments, list) else [],
        "vibes": {
            "status": "not_connected",
            "note": "В снимке нет подтверждённого источника роликов вайбов и статуса закрепления. Список не подменяется пустым значением.",
        },
        "write_actions": {
            "promotion_entry": False,
            "review_pin": False,
            "vibe_pin": False,
            "chat_reply": False,
            "photo_test_launch": False,
        },
    }
