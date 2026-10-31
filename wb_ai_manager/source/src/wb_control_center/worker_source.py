from __future__ import annotations

import asyncio
import csv
import io
import json
import os
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


class WorkerSourceError(RuntimeError):
    pass


class WorkerSource:
    """Read-only bridge to the already-running wb-api-worker.

    The worker owns long-running multi-cabinet finance/FBS collection. WB AI Manager
    consumes its normalized exports instead of duplicating those expensive collectors.
    """

    def __init__(self, base_url: str = "", export_token: str = "", timeout_seconds: float = 30.0,
                 traffic_github_repo: str = "", traffic_github_ref: str = "traffic-state",
                 traffic_private_key_path: str = ""):
        self.base_url = (base_url or "").rstrip("/")
        self.export_token = (export_token or "").strip()
        self.timeout_seconds = max(3.0, float(timeout_seconds))
        self.traffic_github_repo = (traffic_github_repo or "").strip()
        self.traffic_github_ref = (traffic_github_ref or "traffic-state").strip()
        self.traffic_private_key_path = Path(traffic_private_key_path).expanduser() if traffic_private_key_path else None

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
        fact_at = payload.get("finishedAt") or payload.get("updatedAt") or payload.get("generated_at") or payload.get("observed_at")
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

    @property
    def traffic_configured(self) -> bool:
        return bool(self.traffic_github_repo and self.traffic_private_key_path
                    and self.traffic_private_key_path.is_file())

    @staticmethod
    def _csv_from_archive(archive: tarfile.TarFile, suffix: str) -> list[dict[str, str]]:
        member = next((item for item in archive.getmembers()
                      if item.isfile() and item.name.endswith(suffix)), None)
        if member is None:
            return []
        stream = archive.extractfile(member)
        if stream is None:
            return []
        text = stream.read().decode("utf-8-sig")
        return list(csv.DictReader(io.StringIO(text)))

    @staticmethod
    def _json_from_archive(archive: tarfile.TarFile, suffix: str) -> dict[str, Any]:
        member = next((item for item in archive.getmembers()
                      if item.isfile() and item.name.endswith(suffix)), None)
        if member is None:
            return {}
        stream = archive.extractfile(member)
        if stream is None:
            return {}
        value = json.loads(stream.read().decode("utf-8"))
        return value if isinstance(value, dict) else {}

    async def optimizer_bundle(self) -> dict[str, Any]:
        """Read the cabinet-scoped optimizer payload from encrypted GitHub state."""
        if not self.traffic_configured:
            raise WorkerSourceError("GitHub optimizer archive or local private key is not configured")
        root = f"https://raw.githubusercontent.com/{self.traffic_github_repo}/optimizer-state/state"
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            archive_response, key_response = await asyncio.gather(
                client.get(root + "/optimizer-dashboard.tar.gz.enc"),
                client.get(root + "/optimizer-dashboard.key.enc"),
            )
            archive_response.raise_for_status()
            key_response.raise_for_status()
        with tempfile.TemporaryDirectory(prefix="wb-optimizer-github-") as folder:
            folder_path = Path(folder)
            encrypted_archive = folder_path / "optimizer.tar.gz.enc"
            encrypted_key = folder_path / "optimizer.key.enc"
            key_file = folder_path / "optimizer.key"
            plain_archive = folder_path / "optimizer.tar.gz"
            encrypted_archive.write_bytes(archive_response.content)
            encrypted_key.write_bytes(key_response.content)
            unwrap = subprocess.run(
                ["openssl", "pkeyutl", "-decrypt", "-inkey", str(self.traffic_private_key_path),
                 "-in", str(encrypted_key), "-out", str(key_file),
                 "-pkeyopt", "rsa_padding_mode:oaep", "-pkeyopt", "rsa_oaep_md:sha256"],
                capture_output=True, text=True, timeout=20,
            )
            if unwrap.returncode:
                raise WorkerSourceError("GitHub optimizer key could not be decrypted locally")
            os.chmod(key_file, 0o600)
            decrypt = subprocess.run(
                ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-pass", f"file:{key_file}",
                 "-in", str(encrypted_archive), "-out", str(plain_archive)],
                capture_output=True, text=True, timeout=60,
            )
            if decrypt.returncode:
                raise WorkerSourceError("GitHub optimizer archive decryption failed")
            with tarfile.open(plain_archive, "r:gz") as archive:
                for item in archive.getmembers():
                    path = Path(item.name)
                    if path.is_absolute() or ".." in path.parts:
                        raise WorkerSourceError("Unsafe path in GitHub optimizer archive")
                payload = self._json_from_archive(archive, "optimizer-state.json")
        shops = payload.get("shops")
        if payload.get("schema_version") != 1 or not isinstance(shops, dict):
            raise WorkerSourceError("GitHub optimizer archive has an unsupported or incomplete schema")
        return {
            "source": "github-optimizer-state",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "generated_at": payload.get("generated_at"),
            "shops": {key: value for key, value in shops.items()
                      if key in {"ap", "aa", "yv"} and isinstance(value, dict)},
            "query_manager": payload.get("query_manager") if isinstance(payload.get("query_manager"), dict) else {},
        }

    async def traffic_bundle(self) -> dict[str, Any]:
        """Read private, encrypted WB traffic facts stored only in GitHub."""
        if not self.traffic_configured:
            raise WorkerSourceError("GitHub traffic archive or local private key is not configured")
        root = f"https://raw.githubusercontent.com/{self.traffic_github_repo}/{self.traffic_github_ref}/state"
        async with httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=True) as client:
            archive_response, key_response = await asyncio.gather(
                client.get(root + "/traffic-dashboard.tar.gz.enc"),
                client.get(root + "/traffic-dashboard.key.enc"),
            )
            archive_response.raise_for_status()
            key_response.raise_for_status()
        with tempfile.TemporaryDirectory(prefix="wb-traffic-github-") as folder:
            folder_path = Path(folder)
            encrypted_archive = folder_path / "traffic.tar.gz.enc"
            encrypted_key = folder_path / "traffic.key.enc"
            key_file = folder_path / "traffic.key"
            plain_archive = folder_path / "traffic.tar.gz"
            encrypted_archive.write_bytes(archive_response.content)
            encrypted_key.write_bytes(key_response.content)
            unwrap = subprocess.run(
                ["openssl", "pkeyutl", "-decrypt", "-inkey", str(self.traffic_private_key_path),
                 "-in", str(encrypted_key), "-out", str(key_file),
                 "-pkeyopt", "rsa_padding_mode:oaep", "-pkeyopt", "rsa_oaep_md:sha256"],
                capture_output=True, text=True, timeout=20,
            )
            if unwrap.returncode:
                raise WorkerSourceError("GitHub traffic key could not be decrypted locally")
            os.chmod(key_file, 0o600)
            decrypt = subprocess.run(
                ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-pass", f"file:{key_file}",
                 "-in", str(encrypted_archive), "-out", str(plain_archive)],
                capture_output=True, text=True, timeout=60,
            )
            if decrypt.returncode:
                raise WorkerSourceError("GitHub traffic archive decryption failed")
            with tarfile.open(plain_archive, "r:gz") as archive:
                members = archive.getmembers()
                for item in members:
                    path = Path(item.name)
                    if path.is_absolute() or ".." in path.parts:
                        raise WorkerSourceError("Unsafe path in GitHub traffic archive")
                status_member = next((item for item in members if item.isfile() and item.name.endswith("traffic/status.json")), None)
                if status_member is None:
                    raise WorkerSourceError("GitHub traffic archive has no verified status")
                status_stream = archive.extractfile(status_member)
                status = json.loads(status_stream.read().decode("utf-8")) if status_stream else {}
                return {
                    "source": "github-traffic-state",
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "source_health": self.stream_meta(status, 180),
                    "status": status,
                    "clusters_status": self._json_from_archive(archive, "traffic/clusters_status.json"),
                    "campaign_settings_status": self._json_from_archive(archive, "traffic/campaign_settings_status.json"),
                    "ads": self._csv_from_archive(archive, "traffic/campaign_sku_day.csv"),
                    "funnel": self._csv_from_archive(archive, "traffic/funnel_sku_day.csv"),
                    "organic": self._csv_from_archive(archive, "traffic/organic_sku_day.csv"),
                    "clusters": self._csv_from_archive(archive, "traffic/ad_clusters_day.csv"),
                    "campaign_settings": self._csv_from_archive(archive, "traffic/campaign_settings_current.csv"),
                }
