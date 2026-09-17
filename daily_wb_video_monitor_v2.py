#!/usr/bin/env python3
import daily_wb_video_monitor as monitor


def better_content_type(item):
    """Classify Shorts by duration, not merely because YouTube search came from a 'shorts' query."""
    try:
        duration = item.get("duration")
        if duration is not None and int(duration) <= 180:
            return "short"
    except Exception:
        pass
    title = str(item.get("title", "")).lower()
    # Only use the title hashtag as a fallback when duration is unavailable.
    if item.get("duration") is None and ("#shorts" in title or "#short" in title):
        return "short"
    return "video"


def better_relevance(title):
    """Keep seller-specific WB results, but don't require WB to be repeated in every Shorts title."""
    t = f" {str(title).lower()} "
    wb = any(x in t for x in monitor.WB_HINTS)
    seller_score = sum(1 for x in monitor.SELLER_HINTS if x in t)
    edu = any(x in t for x in monitor.EDU_HINTS)
    if wb and seller_score >= 1 and (edu or seller_score >= 2):
        return True
    # Search context is already WB-specific. This catches terse Shorts titles such as
    # 'CPC опять поменяли' / 'Новая ставка в рекламе' without admitting generic clips.
    return seller_score >= 3 and (edu or any(x in t for x in ("cpc", "cpm", "дрр", "drr", "кластер", "ставк", "охват")))


monitor.content_type = better_content_type
monitor.looks_relevant = better_relevance

if __name__ == "__main__":
    monitor.main()
