from __future__ import annotations

import hashlib
import json
from typing import Any

from .advertising_controller import _extract_bid_rub
from .metrics import extract_nm_id, find_dicts_with_any_key, first_number


def _hash(*parts: Any) -> str:
    raw = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _snap(snapshots: dict[str, Any], source: str, key: str) -> Any:
    row = snapshots.get(source, {}).get(key) if isinstance(snapshots.get(source), dict) else None
    if isinstance(row, dict) and "data" in row:
        return row.get("data")
    return row


class ChangeTracker:
    """Detect changes that happened in WB even when this read-only app did not execute them."""

    def __init__(self, db: Any):
        self.db = db

    def _current_state(self, snapshots: dict[str, Any]) -> dict[str, Any]:
        state: dict[str, Any] = {"ad_bids": {}, "prices": {}, "card_media": {}}
        deep = _snap(snapshots, "advertising_optimizer", "deep_scan")
        if isinstance(deep, dict):
            for row in deep.get("campaigns", []) or []:
                if not isinstance(row, dict): continue
                c = row.get("campaign") if isinstance(row.get("campaign"), dict) else {}
                cid = str(row.get("campaign_id") or c.get("advertId") or c.get("advert_id") or c.get("id") or "")
                for nm in row.get("nm_ids", []) or []:
                    try: nm_i=int(nm)
                    except Exception: continue
                    bid=_extract_bid_rub(c, nm_i)
                    if bid is not None:
                        state["ad_bids"][f"{cid}:{nm_i}"] = round(float(bid), 4)

        prices = _snap(snapshots, "price_margin", "prices")
        for d in find_dicts_with_any_key(prices, {"nmId","nmID","price","discountedPrice"}):
            nm=extract_nm_id(d)
            if nm is None: continue
            price=first_number(d,{"discountedPrice","discounted_price","priceWithDisc","price"})
            if price is not None: state["prices"][str(nm)] = round(float(price), 4)

        cards = _snap(snapshots, "cards", "card_catalog")
        for d in find_dicts_with_any_key(cards, {"nmId","nmID","mediaFiles","photos","media"}):
            nm=extract_nm_id(d)
            if nm is None: continue
            media=None
            for k in ("mediaFiles","photos","media","mediafiles"):
                if isinstance(d.get(k), list): media=len(d[k]); break
            if media is not None: state["card_media"][str(nm)] = int(media)
        return state

    def sync(self, snapshots: dict[str, Any]) -> list[dict[str, Any]]:
        current=self._current_state(snapshots)
        previous_raw=self.db.get_kv("change_tracker_state", "{}") or "{}"
        try: previous=json.loads(previous_raw)
        except Exception: previous={}
        changes=[]
        for bucket, ctype, source in (("ad_bids","advert_bid","WB Promotion"),("prices","price","WB Prices"),("card_media","card_media","WB Content")):
            oldb=previous.get(bucket) if isinstance(previous.get(bucket),dict) else {}
            newb=current.get(bucket) if isinstance(current.get(bucket),dict) else {}
            for entity,newv in newb.items():
                if entity not in oldb: continue
                oldv=oldb.get(entity)
                if oldv == newv:
                    # An explicit recommendation to keep the current ad bid is also a
                    # decision worth evaluating.  Require two consecutive observations
                    # of the same real bid before calling it followed; this avoids
                    # treating the first snapshot as evidence of compliance.
                    if ctype == "advert_bid":
                        hist=self.db.latest_unapplied_decision_for_entity(entity)
                        payload=(hist or {}).get("payload") if isinstance(hist,dict) else None
                        current_target=None; recommended_current=None
                        if isinstance(payload,dict):
                            for action in payload.get("recommended_actions",[]) or []:
                                if not isinstance(action,dict) or action.get("mode") != "numeric_ad_plan":
                                    continue
                                value=action.get("value") if isinstance(action.get("value"),dict) else {}
                                try: current_target=float(value.get("target_bid_rub"))
                                except Exception: current_target=None
                                try: recommended_current=float(value.get("current_bid_rub"))
                                except Exception: recommended_current=None
                                break
                        try: observed=float(newv)
                        except Exception: observed=None
                        if current_target is not None and recommended_current is not None and observed is not None:
                            tolerance=max(0.5,abs(current_target)*0.01)
                            if abs(current_target-recommended_current) <= tolerance and abs(observed-current_target) <= tolerance:
                                self.db.mark_latest_decision_held(entity)
                    continue
                fp=_hash(ctype,entity,oldv,newv)
                cid=self.db.record_observed_change(ctype, entity, {"value":oldv}, {"value":newv}, fp, source)
                if cid:
                    change={"id":cid,"change_type":ctype,"entity_id":entity,"old":oldv,"new":newv,"matched_recommendation":False}
                    changes.append(change)

                    # Never teach the agent from an arbitrary manual/external change.
                    # A change becomes "applied recommendation" only when it matches the
                    # latest exact recommendation for this entity.  At present we can
                    # prove this safely for advertising bids because the decision contract
                    # carries an explicit target_bid_rub.  Price/media changes are logged
                    # as context but are not auto-attributed to a recommendation.
                    if ctype == "advert_bid":
                        hist=self.db.latest_unapplied_decision_for_entity(entity)
                        payload=(hist or {}).get("payload") if isinstance(hist,dict) else None
                        target=None
                        if isinstance(payload,dict):
                            for action in payload.get("recommended_actions",[]) or []:
                                if not isinstance(action,dict) or action.get("mode") != "numeric_ad_plan":
                                    continue
                                value=action.get("value") if isinstance(action.get("value"),dict) else {}
                                try: target=float(value.get("target_bid_rub"))
                                except Exception: target=None
                                if target is not None: break
                        try: new_bid=float(newv)
                        except Exception: new_bid=None
                        if target is not None and new_bid is not None:
                            tolerance=max(0.5,abs(target)*0.01)
                            if abs(new_bid-target) <= tolerance:
                                linked=self.db.mark_latest_decision_applied(entity,cid)
                                change["matched_recommendation"] = linked is not None
        self.db.set_kv("change_tracker_state", json.dumps(current, ensure_ascii=False, default=str))
        return changes
