from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .models import ActionProposal, DecisionCard, Event


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL,
  error TEXT
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  agent TEXT NOT NULL,
  severity TEXT NOT NULL,
  event_key TEXT NOT NULL,
  title TEXT NOT NULL,
  message TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  fingerprint TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_fp ON events(fingerprint, created_at);
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  source TEXT NOT NULL,
  snapshot_key TEXT NOT NULL,
  data_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_lookup ON snapshots(source, snapshot_key, created_at);
CREATE TABLE IF NOT EXISTS actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  agent TEXT NOT NULL,
  tool TEXT NOT NULL,
  args_json TEXT NOT NULL,
  reason TEXT NOT NULL,
  risk TEXT NOT NULL,
  status TEXT NOT NULL,
  result_json TEXT,
  error TEXT
);
CREATE INDEX IF NOT EXISTS idx_actions_status ON actions(status, created_at);
CREATE TABLE IF NOT EXISTS decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  decision_key TEXT NOT NULL,
  scope TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  title TEXT NOT NULL,
  diagnosis TEXT NOT NULL,
  priority TEXT NOT NULL,
  confidence TEXT NOT NULL,
  actions_json TEXT NOT NULL,
  evidence_json TEXT NOT NULL,
  blockers_json TEXT NOT NULL,
  follow_up TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_created ON decisions(created_at);
CREATE INDEX IF NOT EXISTS idx_decisions_key ON decisions(decision_key, created_at);
CREATE TABLE IF NOT EXISTS decision_history (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  decision_key TEXT NOT NULL,
  scope TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  recommendation_hash TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  baseline_json TEXT NOT NULL,
  applied_at TEXT,
  applied_change_id INTEGER
);
CREATE INDEX IF NOT EXISTS idx_decision_history_key ON decision_history(decision_key, created_at);
CREATE INDEX IF NOT EXISTS idx_decision_history_entity ON decision_history(entity_id, created_at);
CREATE TABLE IF NOT EXISTS observed_changes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  observed_at TEXT NOT NULL,
  change_type TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  old_json TEXT NOT NULL,
  new_json TEXT NOT NULL,
  fingerprint TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_observed_changes_entity ON observed_changes(entity_id, observed_at);
CREATE TABLE IF NOT EXISTS decision_evaluations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  decision_history_id INTEGER NOT NULL,
  horizon TEXT NOT NULL,
  due_at TEXT NOT NULL,
  evaluated_at TEXT,
  verdict TEXT,
  metrics_json TEXT,
  notes TEXT,
  UNIQUE(decision_history_id, horizon)
);
CREATE INDEX IF NOT EXISTS idx_decision_eval_due ON decision_evaluations(evaluated_at, due_at);
CREATE TABLE IF NOT EXISTS kv (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as con:
            con.executescript(SCHEMA)

    def connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=30)
        con.row_factory = sqlite3.Row
        return con

    def start_run(self, agent: str, started_at: str) -> int:
        with self.connect() as con:
            cur = con.execute("INSERT INTO runs(agent,started_at,status) VALUES(?,?,?)", (agent, started_at, "running"))
            return int(cur.lastrowid)

    def finish_run(self, run_id: int, status: str, finished_at: str, error: str | None = None) -> None:
        with self.connect() as con:
            con.execute("UPDATE runs SET finished_at=?,status=?,error=? WHERE id=?", (finished_at, status, error, run_id))

    def save_event(self, event: Event) -> int:
        with self.connect() as con:
            cur = con.execute(
                "INSERT INTO events(created_at,agent,severity,event_key,title,message,payload_json,fingerprint) VALUES(?,?,?,?,?,?,?,?)",
                (event.created_at, event.agent, event.severity, event.key, event.title, event.message,
                 json.dumps(event.payload, ensure_ascii=False, default=str), event.fingerprint),
            )
            return int(cur.lastrowid)

    def event_seen_recently(self, fingerprint: str, hours: int = 12) -> bool:
        if not fingerprint:
            return False
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self.connect() as con:
            row = con.execute("SELECT 1 FROM events WHERE fingerprint=? AND created_at>=? LIMIT 1", (fingerprint, cutoff)).fetchone()
            return row is not None

    def recent_events(self, hours: int = 24, limit: int = 200) -> list[dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self.connect() as con:
            rows = con.execute("SELECT * FROM events WHERE created_at>=? ORDER BY id DESC LIMIT ?", (cutoff, limit)).fetchall()
        return [dict(r) | {"payload": json.loads(r["payload_json"])} for r in rows]

    def save_snapshot(self, source: str, key: str, data: dict[str, Any], created_at: str) -> int:
        with self.connect() as con:
            cur = con.execute(
                "INSERT INTO snapshots(created_at,source,snapshot_key,data_json) VALUES(?,?,?,?)",
                (created_at, source, key, json.dumps(data, ensure_ascii=False, default=str)),
            )
            return int(cur.lastrowid)

    def latest_snapshot(self, source: str, key: str, before: str | None = None) -> dict[str, Any] | None:
        q = "SELECT data_json,created_at FROM snapshots WHERE source=? AND snapshot_key=?"
        params: list[Any] = [source, key]
        if before:
            q += " AND created_at<?"
            params.append(before)
        q += " ORDER BY id DESC LIMIT 1"
        with self.connect() as con:
            row = con.execute(q, params).fetchone()
        if not row:
            return None
        return {"created_at": row["created_at"], "data": json.loads(row["data_json"])}

    def create_action(self, proposal: ActionProposal) -> int:
        with self.connect() as con:
            cur = con.execute(
                "INSERT INTO actions(created_at,updated_at,agent,tool,args_json,reason,risk,status) VALUES(?,?,?,?,?,?,?,?)",
                (proposal.created_at, proposal.created_at, proposal.agent, proposal.tool,
                 json.dumps(proposal.arguments, ensure_ascii=False, default=str), proposal.reason, proposal.risk, proposal.status),
            )
            return int(cur.lastrowid)

    def get_action(self, action_id: int) -> dict[str, Any] | None:
        with self.connect() as con:
            row = con.execute("SELECT * FROM actions WHERE id=?", (action_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["arguments"] = json.loads(d.pop("args_json"))
        if d.get("result_json"):
            d["result"] = json.loads(d["result_json"])
        return d

    def update_action(self, action_id: int, status: str, result: Any = None, error: str | None = None) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as con:
            con.execute(
                "UPDATE actions SET updated_at=?,status=?,result_json=?,error=? WHERE id=?",
                (now, status, json.dumps(result, ensure_ascii=False, default=str) if result is not None else None, error, action_id),
            )

    def pending_actions(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM actions WHERE status='pending' ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["arguments"] = json.loads(d.pop("args_json"))
            out.append(d)
        return out

    def recent_actions(self, limit: int = 100, statuses: list[str] | None = None) -> list[dict[str, Any]]:
        q = "SELECT * FROM actions"
        params: list[Any] = []
        if statuses:
            q += " WHERE status IN (" + ",".join("?" for _ in statuses) + ")"
            params.extend(statuses)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self.connect() as con:
            rows = con.execute(q, params).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            d["arguments"] = json.loads(d.pop("args_json"))
            if d.get("result_json"):
                d["result"] = json.loads(d["result_json"])
            out.append(d)
        return out

    def recent_runs(self, hours: int = 72, limit: int = 500) -> list[dict[str, Any]]:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        with self.connect() as con:
            rows = con.execute(
                "SELECT * FROM runs WHERE started_at>=? ORDER BY id DESC LIMIT ?",
                (cutoff, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def latest_runs_by_agent(self, hours: int = 720) -> dict[str, dict[str, Any]]:
        rows = self.recent_runs(hours=hours, limit=5000)
        out: dict[str, dict[str, Any]] = {}
        for r in rows:
            out.setdefault(str(r["agent"]), r)
        return out

    def snapshot_keys(self, source: str) -> list[str]:
        with self.connect() as con:
            rows = con.execute(
                "SELECT DISTINCT snapshot_key FROM snapshots WHERE source=? ORDER BY snapshot_key",
                (source,),
            ).fetchall()
        return [str(r["snapshot_key"]) for r in rows]

    def set_kv(self, key: str, value: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self.connect() as con:
            con.execute(
                "INSERT INTO kv(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
                (key, value, now),
            )

    def get_kv(self, key: str, default: str | None = None) -> str | None:
        with self.connect() as con:
            row = con.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def replace_decisions(self, decisions: list[DecisionCard]) -> list[int]:
        ids: list[int] = []
        with self.connect() as con:
            # Decision cards are a current plan, not an append-only event stream.
            con.execute("DELETE FROM decisions")
            for d in decisions:
                cur = con.execute(
                    "INSERT INTO decisions(created_at,decision_key,scope,entity_id,title,diagnosis,priority,confidence,actions_json,evidence_json,blockers_json,follow_up) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (d.created_at, d.decision_key, d.scope, d.entity_id, d.title, d.diagnosis, d.priority, d.confidence,
                     json.dumps(d.recommended_actions, ensure_ascii=False, default=str),
                     json.dumps(d.evidence, ensure_ascii=False, default=str),
                     json.dumps(d.blockers, ensure_ascii=False, default=str), d.follow_up),
                )
                ids.append(int(cur.lastrowid))
        return ids

    def current_decisions(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute(
                "SELECT * FROM decisions ORDER BY CASE priority WHEN 'critical' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END DESC, id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        out=[]
        for r in rows:
            d=dict(r)
            d['recommended_actions']=json.loads(d.pop('actions_json'))
            d['evidence']=json.loads(d.pop('evidence_json'))
            d['blockers']=json.loads(d.pop('blockers_json'))
            out.append(d)
        return out

    def sync_decision_history(self, decisions: list[DecisionCard], baseline_by_key: dict[str, dict[str, Any]] | None = None) -> list[int]:
        """Persist only materially changed decision versions; refresh last_seen otherwise."""
        import hashlib
        now = datetime.now(timezone.utc).isoformat()
        baseline_by_key = baseline_by_key or {}
        ids: list[int] = []
        with self.connect() as con:
            for d in decisions:
                payload = {
                    "decision_key": d.decision_key, "scope": d.scope, "entity_id": d.entity_id,
                    "title": d.title, "diagnosis": d.diagnosis, "priority": d.priority,
                    "confidence": d.confidence, "recommended_actions": d.recommended_actions,
                    "evidence": d.evidence, "blockers": d.blockers, "follow_up": d.follow_up,
                }
                # Hash only the recommendation-bearing fields, not volatile timestamps.
                core = {k: payload[k] for k in ("diagnosis","priority","confidence","recommended_actions","blockers","follow_up")}
                rh = hashlib.sha256(json.dumps(core, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()[:24]
                prev = con.execute(
                    "SELECT id,recommendation_hash FROM decision_history WHERE decision_key=? ORDER BY id DESC LIMIT 1",
                    (d.decision_key,),
                ).fetchone()
                if prev and prev["recommendation_hash"] == rh:
                    con.execute("UPDATE decision_history SET last_seen_at=? WHERE id=?", (now, int(prev["id"])))
                    ids.append(int(prev["id"]))
                    continue
                cur = con.execute(
                    "INSERT INTO decision_history(created_at,last_seen_at,decision_key,scope,entity_id,recommendation_hash,status,payload_json,baseline_json) VALUES(?,?,?,?,?,?,?,?,?)",
                    (now, now, d.decision_key, d.scope, d.entity_id, rh, "recommended",
                     json.dumps(payload, ensure_ascii=False, default=str),
                     json.dumps(baseline_by_key.get(d.decision_key, {}), ensure_ascii=False, default=str)),
                )
                hid = int(cur.lastrowid)
                ids.append(hid)
                for horizon, hours in (("1ч",1),("3ч",3),("24ч",24),("7д",168),("14д",336)):
                    due = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
                    con.execute(
                        "INSERT OR IGNORE INTO decision_evaluations(decision_history_id,horizon,due_at) VALUES(?,?,?)",
                        (hid, horizon, due),
                    )
        return ids

    def decision_history(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM decision_history ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out=[]
        for r in rows:
            d=dict(r)
            d["payload"] = json.loads(d.pop("payload_json"))
            d["baseline"] = json.loads(d.pop("baseline_json"))
            out.append(d)
        return out

    def record_observed_change(self, change_type: str, entity_id: str, old: dict[str, Any], new: dict[str, Any], fingerprint: str, source: str) -> int | None:
        now = datetime.now(timezone.utc).isoformat()
        try:
            with self.connect() as con:
                cur = con.execute(
                    "INSERT INTO observed_changes(observed_at,change_type,entity_id,old_json,new_json,fingerprint,source) VALUES(?,?,?,?,?,?,?)",
                    (now, change_type, entity_id, json.dumps(old, ensure_ascii=False, default=str), json.dumps(new, ensure_ascii=False, default=str), fingerprint, source),
                )
                return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            return None

    def recent_observed_changes(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows = con.execute("SELECT * FROM observed_changes ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        out=[]
        for r in rows:
            d=dict(r); d["old"]=json.loads(d.pop("old_json")); d["new"]=json.loads(d.pop("new_json")); out.append(d)
        return out

    def latest_unapplied_decision_for_entity(self, entity_id: str) -> dict[str, Any] | None:
        """Return the latest unapplied decision payload for an exact entity.

        ChangeTracker uses this to prove that a real-world change actually matches the
        agent recommendation before the decision is marked as applied/training evidence.
        """
        with self.connect() as con:
            row = con.execute(
                "SELECT * FROM decision_history WHERE entity_id=? AND applied_at IS NULL ORDER BY id DESC LIMIT 1",
                (entity_id,),
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["payload"] = json.loads(d.pop("payload_json"))
        d["baseline"] = json.loads(d.pop("baseline_json"))
        return d

    def _mark_latest_decision_followed(self, entity_id: str, status: str, change_id: int | None = None, applied_at: str | None = None) -> int | None:
        applied_at = applied_at or datetime.now(timezone.utc).isoformat()
        with self.connect() as con:
            row = con.execute(
                "SELECT id FROM decision_history WHERE entity_id=? AND applied_at IS NULL ORDER BY id DESC LIMIT 1",
                (entity_id,),
            ).fetchone()
            if not row:
                return None
            hid=int(row["id"])
            con.execute("UPDATE decision_history SET status=?,applied_at=?,applied_change_id=? WHERE id=?", (status, applied_at, change_id, hid))
            # Re-anchor evaluation due dates to the moment the recommendation was
            # actually observed as followed (including an explicit no-change/hold).
            base=datetime.fromisoformat(applied_at)
            for horizon, hours in (("1ч",1),("3ч",3),("24ч",24),("7д",168),("14д",336)):
                con.execute("UPDATE decision_evaluations SET due_at=? WHERE decision_history_id=? AND horizon=? AND evaluated_at IS NULL", ((base+timedelta(hours=hours)).isoformat(), hid, horizon))
            return hid

    def mark_latest_decision_applied(self, entity_id: str, change_id: int, applied_at: str | None = None) -> int | None:
        return self._mark_latest_decision_followed(entity_id, "observed_applied", change_id, applied_at)

    def mark_latest_decision_held(self, entity_id: str, observed_at: str | None = None) -> int | None:
        return self._mark_latest_decision_followed(entity_id, "observed_hold", None, observed_at)

    def observed_changes_since(self, entity_id: str, since: str) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows=con.execute(
                "SELECT * FROM observed_changes WHERE entity_id=? AND observed_at>? ORDER BY observed_at,id",
                (entity_id,since),
            ).fetchall()
        out=[]
        for r in rows:
            d=dict(r); d["old"]=json.loads(d.pop("old_json")); d["new"]=json.loads(d.pop("new_json")); out.append(d)
        return out

    def due_evaluations(self, now_iso: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        now_iso = now_iso or datetime.now(timezone.utc).isoformat()
        with self.connect() as con:
            rows=con.execute(
                "SELECT e.*,h.decision_key,h.entity_id,h.payload_json,h.baseline_json,h.applied_at FROM decision_evaluations e JOIN decision_history h ON h.id=e.decision_history_id WHERE e.evaluated_at IS NULL AND h.applied_at IS NOT NULL AND e.due_at<=? ORDER BY e.due_at LIMIT ?",
                (now_iso,limit),
            ).fetchall()
        out=[]
        for r in rows:
            d=dict(r); d["payload"]=json.loads(d.pop("payload_json")); d["baseline"]=json.loads(d.pop("baseline_json")); out.append(d)
        return out

    def complete_evaluation(self, evaluation_id: int, verdict: str, metrics: dict[str, Any], notes: str = "") -> None:
        now=datetime.now(timezone.utc).isoformat()
        with self.connect() as con:
            con.execute("UPDATE decision_evaluations SET evaluated_at=?,verdict=?,metrics_json=?,notes=? WHERE id=?", (now, verdict, json.dumps(metrics, ensure_ascii=False, default=str), notes, evaluation_id))

    def recent_evaluations(self, limit: int = 200) -> list[dict[str, Any]]:
        with self.connect() as con:
            rows=con.execute("SELECT e.*,h.decision_key,h.entity_id FROM decision_evaluations e JOIN decision_history h ON h.id=e.decision_history_id WHERE e.evaluated_at IS NOT NULL ORDER BY e.id DESC LIMIT ?", (limit,)).fetchall()
        out=[]
        for r in rows:
            d=dict(r); d["metrics"]=json.loads(d.pop("metrics_json")) if d.get("metrics_json") else {}; out.append(d)
        return out

