from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Iterable, Mapping


class Stage(str, Enum):
    NEW = "NEW"
    WB_CONNECTED = "WB_CONNECTED"
    SM_CONNECTED = "SM_CONNECTED"
    SKU_READY = "SKU_READY"
    BACKFILL = "BACKFILL"
    ANALYTICS = "ANALYTICS"
    QC = "QC"
    READY = "READY"
    BLOCKED = "BLOCKED"
    ERROR = "ERROR"


PIPELINE = (
    Stage.NEW,
    Stage.WB_CONNECTED,
    Stage.SM_CONNECTED,
    Stage.SKU_READY,
    Stage.BACKFILL,
    Stage.ANALYTICS,
    Stage.QC,
    Stage.READY,
)


@dataclass
class ClientState:
    client_id: str
    spreadsheet_id: str
    stage: Stage = Stage.NEW
    retry_count: int = 0
    last_success_at: str = ""
    next_run_at: str = ""
    lock_owner: str = ""
    lock_until: str = ""
    error: str = ""
    qc_status: str = "NOT_RUN"
    ready_at: str = ""

    def json(self) -> dict:
        out = asdict(self)
        out["stage"] = self.stage.value
        return out


@dataclass(frozen=True)
class QCResult:
    ok: bool
    checks: Mapping[str, bool]
    error: str = ""


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def acquire_lock(state: ClientState, owner: str, ttl_minutes: int = 12, now: datetime | None = None) -> bool:
    now = now or utcnow()
    until = parse_ts(state.lock_until)
    if state.lock_owner and until and until > now and state.lock_owner != owner:
        return False
    state.lock_owner = owner
    state.lock_until = (now + timedelta(minutes=ttl_minutes)).isoformat()
    return True


def release_lock(state: ClientState, owner: str) -> None:
    if state.lock_owner == owner:
        state.lock_owner = ""
        state.lock_until = ""


def schedule_retry(state: ClientState, message: str, now: datetime | None = None, cap_minutes: int = 180) -> None:
    now = now or utcnow()
    state.retry_count += 1
    delay = min(cap_minutes, 2 ** min(state.retry_count, 8))
    state.next_run_at = (now + timedelta(minutes=delay)).isoformat()
    state.error = message[:1500]
    state.stage = Stage.ERROR


def next_stage(stage: Stage) -> Stage:
    if stage in {Stage.BLOCKED, Stage.ERROR, Stage.READY}:
        return stage
    idx = PIPELINE.index(stage)
    return PIPELINE[min(idx + 1, len(PIPELINE) - 1)]


def evaluate_qc(metrics: Mapping[str, object]) -> QCResult:
    required = {
        "wb_connected": bool(metrics.get("wb_connected")),
        "sm_connected": bool(metrics.get("sm_connected")),
        "sku_count_positive": int(metrics.get("sku_count") or 0) > 0,
        "backfill_complete": bool(metrics.get("backfill_complete")),
        "d1_complete": bool(metrics.get("d1_complete")),
        "rnp_complete": bool(metrics.get("rnp_complete")),
        "ads_complete": bool(metrics.get("ads_complete")),
        "traffic_complete": bool(metrics.get("traffic_complete")),
        "search_complete": bool(metrics.get("search_complete")),
        "stocks_complete": bool(metrics.get("stocks_complete")),
        "no_duplicates": int(metrics.get("duplicate_keys") or 0) == 0,
        "formula_errors_zero": int(metrics.get("formula_errors") or 0) == 0,
        "stale_jobs_zero": int(metrics.get("stale_jobs") or 0) == 0,
    }
    bad = [name for name, ok in required.items() if not ok]
    if bad:
        return QCResult(False, required, "QC_FAILED: " + ",".join(bad))
    return QCResult(True, required)


def advance(
    state: ClientState,
    handlers: Mapping[Stage, Callable[[ClientState], Mapping[str, object]]],
    *,
    owner: str,
    now: datetime | None = None,
) -> tuple[ClientState, dict]:
    now = now or utcnow()
    if not acquire_lock(state, owner, now=now):
        return state, {"ok": False, "skipped": True, "reason": "lock_busy"}

    try:
        if state.stage == Stage.READY:
            return state, {"ok": True, "ready": True, "idempotent": True}

        if state.stage in {Stage.BLOCKED, Stage.ERROR}:
            state.stage = Stage.NEW if state.stage == Stage.ERROR else Stage.BLOCKED
            if state.stage == Stage.BLOCKED:
                return state, {"ok": False, "blocked": True, "error": state.error}

        current = state.stage
        handler = handlers.get(current)
        if not handler:
            raise RuntimeError(f"missing handler for {current.value}")

        result = dict(handler(state))
        if not result.get("ok"):
            if result.get("blocked"):
                state.stage = Stage.BLOCKED
                state.error = str(result.get("error") or "blocked")[:1500]
                return state, result
            raise RuntimeError(str(result.get("error") or f"{current.value} failed"))

        if current == Stage.QC:
            qc = evaluate_qc(result)
            state.qc_status = "PASS" if qc.ok else "FAIL"
            if not qc.ok:
                raise RuntimeError(qc.error)

        state.stage = next_stage(current)
        state.retry_count = 0
        state.error = ""
        state.next_run_at = ""
        state.last_success_at = now.isoformat()
        if state.stage == Stage.READY:
            state.ready_at = now.isoformat()
        return state, {"ok": True, "completed": current.value, "next": state.stage.value, **result}
    except Exception as exc:
        schedule_retry(state, str(exc), now=now)
        return state, {"ok": False, "error": str(exc), "next_run_at": state.next_run_at}
    finally:
        release_lock(state, owner)


def run_until_terminal(
    state: ClientState,
    handlers: Mapping[Stage, Callable[[ClientState], Mapping[str, object]]],
    *,
    owner: str,
    max_steps: int = 20,
) -> tuple[ClientState, list[dict]]:
    history: list[dict] = []
    for _ in range(max_steps):
        if state.stage in {Stage.READY, Stage.BLOCKED}:
            break
        if state.stage == Stage.ERROR:
            state.stage = Stage.NEW
        state, result = advance(state, handlers, owner=owner)
        history.append(result)
        if not result.get("ok"):
            break
    return state, history
