from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # WB data source: live = real Seller API through wb-mcp-server; demo = built-in fake cabinet.
    wb_mode: str = "live"
    wb_api_token: str = ""
    wb_shop_id: str | None = None

    # Reasoning layer. rules requires no LLM/API key. ollama is fully local.
    llm_provider: str = "rules"
    llm_model: str = ""
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    ollama_base_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "qwen3:4b-instruct"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Optional read-only Google Sheets bridge (Apps Script Web App).
    google_sheets_bridge_url: str = ""
    google_sheets_bridge_key: str = ""

    app_host: str = "127.0.0.1"
    app_port: int = 8787
    app_timezone: str = "Europe/Moscow"
    data_dir: str = "./data"
    log_level: str = "INFO"

    # shadow = never write; approval = human-confirmed writes; guarded_auto = only allowlisted safe writes.
    operation_mode: str = "shadow"
    auto_actions: bool = False  # deprecated compatibility flag; operation_mode is authoritative
    # Safety fuse for the test/read-only product build. When True, no WB write may execute
    # even if operation_mode is accidentally changed to approval/guarded_auto.
    force_read_only: bool = True
    enable_public_wb_search: bool = True
    # Public storefront search is geo-sensitive. Keep the destination explicit and
    # configurable so competitor evidence never silently pretends to be universal.
    public_wb_dest: str = "-1257786"

    # Hot business-policy sync. This lets strategy change without reinstalling the app.
    remote_policy_url: str = "https://raw.githubusercontent.com/v88218429-netizen/zzzz/mainggg/wb_ai_manager/policy.json"
    remote_policy_refresh_seconds: int = 60

    # Safe self-update channel. Rules hot-reload independently; code updates are
    # staged, checksum-verified, smoke-tested and rolled back on failure.
    auto_update_enabled: bool = True
    auto_update_manifest_url: str = "https://raw.githubusercontent.com/v88218429-netizen/zzzz/mainggg/wb_ai_manager/update-manifest.json"
    auto_update_channel: str = "stable"
    auto_update_check_seconds: int = 60

    # Hard runtime bounds. A dead connector/agent must never hold its lock forever.
    mcp_start_timeout_seconds: float = 45.0
    mcp_call_timeout_seconds: float = 90.0
    agent_run_timeout_seconds: float = 300.0

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cloud_llm_enabled(self) -> bool:
        return self.llm_provider.lower() in {"openai", "anthropic"}


@dataclass
class PolicyConfig:
    raw: dict[str, Any]

    @property
    def thresholds(self) -> dict[str, Any]:
        return self.raw.get("thresholds", {})

    @property
    def schedules(self) -> dict[str, int]:
        return self.raw.get("schedules", {})

    @property
    def safety(self) -> dict[str, Any]:
        return self.raw.get("safety", {})


@dataclass
class CatalogItem:
    nm_id: int
    vendor_code: str = ""
    cost_rub: float | None = None
    min_price_rub: float | None = None
    target_margin_pct: float | None = None
    priority: str = ""


@dataclass
class WatchQuery:
    text: str
    own_nm_ids: list[int] = field(default_factory=list)
    competitors: list[int] = field(default_factory=list)


def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_policy(path: str | Path | None = None) -> PolicyConfig:
    path = Path(path) if path else project_root() / "config" / "policies.yaml"
    with path.open("r", encoding="utf-8") as f:
        return PolicyConfig(yaml.safe_load(f) or {})


def load_catalog(path: str | Path | None = None) -> dict[int, CatalogItem]:
    path = Path(path) if path else project_root() / "config" / "catalog.csv"
    if not path.exists():
        return {}
    items: dict[int, CatalogItem] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            try:
                nm = int(row.get("nm_id") or 0)
            except ValueError:
                continue
            if not nm:
                continue

            def fnum(k: str) -> float | None:
                v = (row.get(k) or "").strip()
                return float(v.replace(",", ".")) if v else None

            items[nm] = CatalogItem(
                nm_id=nm,
                vendor_code=(row.get("vendor_code") or "").strip(),
                cost_rub=fnum("cost_rub"),
                min_price_rub=fnum("min_price_rub"),
                target_margin_pct=fnum("target_margin_pct"),
                priority=(row.get("priority") or "").strip(),
            )
    return items


def load_watch_queries(path: str | Path | None = None) -> list[WatchQuery]:
    path = Path(path) if path else project_root() / "config" / "watch_queries.yaml"
    if not path.exists():
        return []
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[WatchQuery] = []
    for q in raw.get("queries", []):
        if not q.get("text"):
            continue
        out.append(
            WatchQuery(
                text=str(q["text"]),
                own_nm_ids=[int(x) for x in q.get("own_nm_ids", [])],
                competitors=[int(x) for x in q.get("competitors", [])],
            )
        )
    return out
