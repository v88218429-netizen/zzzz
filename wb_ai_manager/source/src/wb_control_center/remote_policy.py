from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

from . import __version__
from typing import Any


class RemotePolicyClient:
    """Small cached reader for a remote JSON policy.

    No secret is required for the current public GitHub test repository. Production
    can point REMOTE_POLICY_URL to an authenticated/private endpoint later.
    """

    def __init__(self, url: str | None, cache_path: Path, refresh_seconds: int = 60):
        self.url = (url or "").strip()
        self.cache_path = cache_path
        self.refresh_seconds = max(15, int(refresh_seconds or 60))
        self._last_attempt = 0.0
        self._last_error: str | None = None
        self._last_loaded_at: float | None = None
        self._current: dict[str, Any] = self._read_cache()

    def _read_cache(self) -> dict[str, Any]:
        try:
            x=json.loads(self.cache_path.read_text(encoding="utf-8"))
            return x if isinstance(x,dict) else {}
        except Exception:
            return {}

    def get(self, force: bool = False) -> dict[str, Any]:
        if not self.url:
            return self._current
        now=time.time()
        if not force and now-self._last_attempt < self.refresh_seconds:
            return self._current
        self._last_attempt=now
        try:
            req=urllib.request.Request(self.url, headers={"User-Agent":f"WB-AI-Manager/{__version__}","Cache-Control":"no-cache"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw=resp.read().decode("utf-8")
            data=json.loads(raw)
            if not isinstance(data,dict):
                raise ValueError("remote policy root must be object")
            self._current=data
            self.cache_path.parent.mkdir(parents=True,exist_ok=True)
            self.cache_path.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
            self._last_error=None
            self._last_loaded_at=now
        except Exception as exc:
            self._last_error=str(exc)
        return self._current

    def status(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.url),
            "url": self.url,
            "version": self._current.get("version") if isinstance(self._current,dict) else None,
            "last_error": self._last_error,
            "last_loaded_at_epoch": self._last_loaded_at,
            "refresh_seconds": self.refresh_seconds,
        }
