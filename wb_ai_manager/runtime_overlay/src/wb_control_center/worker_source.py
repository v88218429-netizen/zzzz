from __future__ import annotations

import csv
import io
from datetime import datetime, timezone
from typing import Any

import httpx


class WorkerSourceError(RuntimeError):
    pass


class WorkerSource:
    """Read-only bridge to the already-running wb-api-worker.

    The worker owns long-running multi-cabinet finance/FBS collection. WB AI Manager
    consumes its normalized exports instead of duplicating those expensive collectors.
    """

    def __init__(self, base_url: str = "", export_token: str = "", timeout_seconds: float = 30.0):
        self.base_url = (base_url or "").rstrip("/")
        self.export_token = (export_token or "").strip()
        self.timeout_seconds = max(3.0, float(timeout_seconds))

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.export_token)

    def _params(self) -> dict[str, str]:
        if not self.configured:
            raise WorkerSourceError("wb-api-worker bridge is not configured")
        return {"token": self.export_token}

    async def _get_json(self, path: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = await client.get(self.base_url + path, params=self._params())
            response.raise_for_status()
            data = response.json()
        return data if isinstance(data, dict) else {"data": data}

    async def _get_csv(self, path: str, limit: int = 10000) -> list[dict[str, str]]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = await client.get(self.base_url + path, params=self._params())
            response.raise_for_status()
            text = response.text
        rows = list(csv.DictReader(io.StringIO(text.lstrip("\ufeff"))))
        return rows[: max(1, int(limit))]

    @staticmethod
    def _as_dt(value: Any) -> datetime | None:
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return None

    @classmethod
    def stream_meta(cls, payload: dict[str, Any], max_age_minutes: int) -> dict[str, Any]:
        fact_at = payload.get("finishedAt") or payload.get("updatedAt") or payload.get("generated_at")
        dt = cls._as_dt(fact_at)
        now = datetime.now(timezone.utc)
        age_minutes = None if dt is None else max(0.0, (now - dt.astimezone(timezone.utc)).total_seconds() / 60.0)

        phase = str(payload.get("phase") or payload.get("state") or "").lower()
        shops = payload.get("shops") if isinstance(payload.get("shops"), dict) else {}
        shop_states = [bool(v.get("ok")) for v in shops.values() if isinstance(v, dict) and "ok" in v]

        if phase == "error" or payload.get("ok") is False:
            status = "ERROR"
        elif shop_states and not all(shop_states):
            status = "PARTIAL"
        elif dt is None:
            status = "UNRELIABLE"
        elif age_minutes is not None and age_minutes > max_age_minutes:
            status = "STALE"
        else:
            status = "FRESH"

        return {
            "status": status,
            "fact_updated_at": fact_at,
            "age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
            "max_age_minutes": int(max_age_minutes),
        }

    async def health_summary(self) -> dict[str, Any]:
        if not self.configured:
            return {
                "source": "wb-api-worker",
                "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "configured": False,
                "status": "UNAVAILABLE",
                "streams": {},
            }

        checks = {
            "finance": ("/api/finance/status", 480),
            "fbs_supplies": ("/api/fbs/status", 120),
            "fbs_penalty_evidence": ("/api/fbs/penalty-evidence-status", 120),
        }
        streams: dict[str, Any] = {}
        for name, (path, max_age) in checks.items():
            try:
                payload = await self._get_json(path)
                streams[name] = self.stream_meta(payload, max_age) | {"payload": payload}
            except Exception as exc:
                streams[name] = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
        states = [str(v.get("status")) for v in streams.values()]
        overall = "FRESH"
        for candidate in ("ERROR", "UNAVAILABLE", "UNRELIABLE", "STALE", "PARTIAL"):
            if candidate in states:
                overall = candidate
                break
        return {
            "source": "wb-api-worker",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "configured": True,
            "status": overall,
            "streams": streams,
        }

    async def finance_bundle(self) -> dict[str, Any]:
        status = await self._get_json("/api/finance/status")
        charges = await self._get_csv("/api/finance/charges.csv", limit=10000)
        reports = await self._get_csv("/api/finance/reports.csv", limit=5000)
        return {
            "source": "wb-api-worker",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "source_health": self.stream_meta(status, 480),
            "status": status,
            "charges": charges,
            "reports": reports,
        }

    async def fbs_bundle(self) -> dict[str, Any]:
        supply_status = await self._get_json("/api/fbs/status")
        evidence_status = await self._get_json("/api/fbs/penalty-evidence-status")
        supplies = await self._get_csv("/api/fbs/supplies.csv", limit=10000)
        evidence = await self._get_csv("/api/fbs/penalty-evidence.csv", limit=10000)
        return {
            "source": "wb-api-worker",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "supply_health": self.stream_meta(supply_status, 120),
            "evidence_health": self.stream_meta(evidence_status, 120),
            "supply_status": supply_status,
            "evidence_status": evidence_status,
            "supplies": supplies,
            "penalty_evidence": evidence,
        }
