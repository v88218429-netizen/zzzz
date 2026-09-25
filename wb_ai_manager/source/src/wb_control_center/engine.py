from __future__ import annotations

import asyncio
from dataclasses import asdict
import logging
from datetime import datetime, timezone
from typing import Any

from .agents import AGENT_CLASSES
from .agents.base import AgentContext, reset_analysis_period, set_analysis_period
from .config import Settings, load_policy
from .db import Database
from .decision_engine import DecisionEngine
from .portfolio import PortfolioService
from .llm import LLMClient
from .mcp_client import WBMCPClient
from .worker_source import WorkerSource
from .demo_wb import DemoWBClient
from .models import ActionProposal, Event, PeriodContext
from .notifier import TelegramNotifier
from .policy import PolicyEngine
from .runtime_policy import RuntimePolicyStore
from .remote_policy import RemotePolicyClient
from .operating_model import OperatingModel
from .decision_review import DecisionReviewBoard
from .change_tracker import ChangeTracker
from .outcome_evaluator import OutcomeEvaluator
from .investigation import InvestigationEngine
from .model_validation import DemandModelValidator

log = logging.getLogger(__name__)


class ControlCenter:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.policy = load_policy()
        self.db = Database(settings.data_path / "control_center.sqlite3")
        self.remote_policy = RemotePolicyClient(settings.remote_policy_url, settings.data_path / "remote_policy_cache.json", settings.remote_policy_refresh_seconds)
        self.runtime_policy = RuntimePolicyStore(self.db, self.policy, self.remote_policy)
        self.wb = DemoWBClient() if settings.wb_mode.lower() == "demo" else WBMCPClient(settings.wb_api_token, str(settings.data_path / "wb_mcp"), settings.wb_shop_id, start_timeout=settings.mcp_start_timeout_seconds, call_timeout=settings.mcp_call_timeout_seconds)
        self.worker = WorkerSource(settings.wb_worker_base_url, settings.wb_worker_export_token, settings.wb_worker_timeout_seconds)
        self.llm = LLMClient(settings)
        self.notifier = TelegramNotifier(settings)
        self.policy_engine = PolicyEngine(settings, self.policy)
        self.portfolio = PortfolioService(settings)
        self.decision_engine = DecisionEngine(self.policy)
        self.operating_model = OperatingModel()
        self.review_board = DecisionReviewBoard(self.policy)
        self.change_tracker = ChangeTracker(self.db)
        self.outcome_evaluator = OutcomeEvaluator(self.db)
        self.investigation_engine = InvestigationEngine()
        self.demand_validator = DemandModelValidator()
        self.ctx = AgentContext(settings=settings, policy=self.policy, db=self.db, wb=self.wb, llm=self.llm, worker=self.worker)
        self.agents = {name: cls(self.ctx) for name, cls in AGENT_CLASSES.items()}
        self.scheduler = None
        self._telegram_task: asyncio.Task | None = None
        self._startup_audit_task: asyncio.Task | None = None
        self._started = False
        self._agent_locks = {name: asyncio.Lock() for name in self.agents}
        self._run_all_lock = asyncio.Lock()
        self._action_locks: dict[int, asyncio.Lock] = {}
        self._period_audit_locks: dict[str, asyncio.Lock] = {}
        self._period_tasks: dict[str, asyncio.Task] = {}

    async def start(self) -> None:
        if self._started:
            return
        try:
            await self.wb.start()
        except Exception as exc:
            # Degraded read-only mode: Sheets/public-web/Decision Engine continue even
            # when Seller API is not connected yet. Individual WB agents will report
            # their own source errors instead of killing the whole control center.
            log.warning("WB Seller API unavailable; continuing in degraded read-only mode: %s", exc)
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            scheduler_cls = AsyncIOScheduler
        except ImportError:
            from .simple_scheduler import SimpleAsyncScheduler
            scheduler_cls = SimpleAsyncScheduler
        self.scheduler = scheduler_cls(timezone=self.settings.app_timezone)
        self._install_jobs()
        self.scheduler.start()
        if self.notifier.enabled:
            self._telegram_task = asyncio.create_task(
                self.notifier.poll_commands(self.approve_action_text, self.reject_action_text, self.status_text)
            )
        self._started = True
        if self.settings.startup_audit_enabled:
            self._startup_audit_task = asyncio.create_task(self.run_all_once())

    async def stop(self) -> None:
        if self.scheduler is not None and self.scheduler.running:
            self.scheduler.shutdown(wait=False)
        if self._telegram_task:
            self.notifier.stop()
            self._telegram_task.cancel()
            try:
                await self._telegram_task
            except BaseException:
                pass
        if self._startup_audit_task:
            self._startup_audit_task.cancel()
            try:
                await self._startup_audit_task
            except BaseException:
                pass
            self._startup_audit_task = None
        await self.wb.close()
        self._started = False

    def _install_jobs(self) -> None:
        assert self.scheduler is not None
        for name, minutes in self.policy.schedules.items():
            if name not in self.agents:
                continue
            self.scheduler.add_job(
                self.run_agent,
                "interval",
                minutes=int(minutes),
                id=f"agent:{name}",
                args=[name],
                max_instances=1,
                coalesce=True,
                misfire_grace_time=max(60, int(minutes) * 60),
            )
        # Daily executive-style supervisor digest in the morning and evening.
        self.scheduler.add_job(self.run_agent, "cron", hour=9, minute=5, id="supervisor:morning", args=["supervisor"], max_instances=1)
        self.scheduler.add_job(self.run_agent, "cron", hour=19, minute=5, id="supervisor:evening", args=["supervisor"], max_instances=1)

    async def run_agent(self, name: str) -> dict[str, Any]:
        if name not in self.agents:
            raise KeyError(name)
        lock = self._agent_locks[name]
        if lock.locked():
            return {"agent": name, "status": "skipped", "reason": "already running"}
        async with lock:
            started = datetime.now(timezone.utc).isoformat()
            run_id = self.db.start_run(name, started)
            try:
                try:
                    result = await asyncio.wait_for(self.agents[name].run(), timeout=max(1.0, float(self.settings.agent_run_timeout_seconds)))
                except asyncio.TimeoutError as exc:
                    raise TimeoutError(f"agent {name} exceeded {self.settings.agent_run_timeout_seconds:g}s runtime limit") from exc
                # Save snapshots before events so later agents can compare immediately.
                for key, data in result.snapshots:
                    self.db.save_snapshot(name, key, data, datetime.now(timezone.utc).isoformat())
                event_ids = []
                for event in result.events:
                    if event.fingerprint and self.db.event_seen_recently(event.fingerprint, hours=6):
                        continue
                    event_ids.append(self.db.save_event(event))
                    if event.severity in {"warning", "critical"} or name == "supervisor":
                        try:
                            await self.notifier.send_event(event)
                        except Exception as e:
                            log.warning("notification failed: %s", e)
                action_ids = []
                for proposal in result.actions:
                    ok, why = self.policy_engine.validate_action(proposal)
                    if not ok:
                        blocked = Event(
                            agent=name,
                            severity="warning",
                            key=f"blocked_action:{proposal.tool}",
                            title="Действие заблокировано safety-policy",
                            message=f"{proposal.tool}: {why}. Причина предложения: {proposal.reason}",
                            payload={"arguments": proposal.arguments},
                        )
                        self.db.save_event(blocked)
                        continue
                    action_id = self.db.create_action(proposal)
                    action_ids.append(action_id)
                    if self.policy_engine.can_auto_execute(proposal):
                        await self.execute_action(action_id, approved_by="policy")
                    else:
                        try:
                            await self.notifier.send_action(action_id, proposal.tool, proposal.reason, proposal.risk)
                        except Exception as e:
                            log.warning("action notification failed: %s", e)
                self.db.finish_run(run_id, "ok", datetime.now(timezone.utc).isoformat())
                try:
                    self.refresh_decisions()
                except Exception as exc:
                    log.warning("decision refresh after %s failed: %s", name, exc)
                return {"agent": name, "status": "ok", "events": event_ids, "actions": action_ids, "snapshots": len(result.snapshots)}
            except Exception as e:
                log.exception("agent %s failed", name)
                self.db.finish_run(run_id, "error", datetime.now(timezone.utc).isoformat(), str(e))
                ev = Event(
                    agent=name,
                    severity="warning",
                    key="agent_failed",
                    title=f"Агент {name} завершился с ошибкой",
                    message=str(e),
                    payload={},
                )
                self.db.save_event(ev)
                try:
                    await self.notifier.send_event(ev)
                except Exception:
                    pass
                return {"agent": name, "status": "error", "error": str(e)}

    async def run_all_once(self) -> list[dict[str, Any]]:
        if self._run_all_lock.locked():
            return [{"system": "run_all", "status": "skipped", "reason": "full audit already running"}]
        async with self._run_all_lock:
            return await self._run_all_once_locked()

    async def _run_all_once_locked(self) -> list[dict[str, Any]]:
        # Run health first, supervisor last. Others sequentially to be kind to WB rate limits.
        order = [
            "api_health", "cards", "advertising_monitor", "advertising_optimizer",
            "inventory", "supply", "funnel", "search_positions", "price_margin",
            "finance", "cost_guard", "reviews_questions", "buyer_chats", "orders_fbs", "returns_quality",
            "documents", "competitors", "experiments", "supervisor",
        ]
        results = []
        for name in order:
            results.append(await self.run_agent(name))
            await asyncio.sleep(0.5)
        decisions=self.refresh_decisions()
        results.append({"system":"decision_engine","status":"ok","decisions":len(decisions)})
        return results

    def refresh_decisions(self) -> list[dict[str, Any]]:
        snap = self.portfolio.snapshot()
        # Cross-contour evidence graph: every detector can contribute facts, while
        # only DecisionEngine is allowed to formulate the final recommendation.
        ss: dict[str, Any] = {}
        for source in self.agents:
            keys = self.db.snapshot_keys(source)
            if not keys:
                continue
            ss[source] = {}
            for key in keys:
                item = self.db.latest_snapshot(source, key)
                if item:
                    ss[source][key] = item
        ss["_events"] = self.db.recent_events(hours=48, limit=500)
        ss["_operating_findings"] = [x.to_dict() for x in self.operating_model.build(snap)]
        cards = self.decision_engine.build(snap, ss)
        cards, reviews = self.review_board.review(cards, snap, ss)
        self.db.replace_decisions(cards)
        baseline_by_key: dict[str, dict[str, Any]] = {}
        for c in cards:
            metrics: dict[str, Any] = {}
            for e in c.evidence:
                if isinstance(e, dict) and e.get("metric") is not None:
                    metrics[str(e.get("metric"))] = e.get("value")
            baseline_by_key[c.decision_key] = metrics
        self.db.sync_decision_history(cards, baseline_by_key)
        changes = self.change_tracker.sync(ss)
        evaluations = self.outcome_evaluator.evaluate_due(cards)
        investigations = self.investigation_engine.build(cards)
        validation = self.demand_validator.validate(snap)
        now = datetime.now(timezone.utc).isoformat()
        self.db.save_snapshot("decision_review", "latest", {
            "reviews": [x.to_dict() for x in reviews],
            "investigations": [x.to_dict() for x in investigations],
            "observed_changes": changes,
            "evaluations_completed": evaluations,
        }, now)
        self.db.save_snapshot("model_validation", "demand", validation, now)
        return self.db.current_decisions()

    @staticmethod
    def _period_event_scope(agent: str) -> str:
        if agent in {"advertising_monitor", "advertising_optimizer", "funnel", "search_positions", "finance", "cost_guard", "documents"}:
            return "selected_period"
        if agent in {"inventory", "price_margin"}:
            return "current_plus_period"
        return "current_snapshot"

    @staticmethod
    def _period_snapshot_scope(agent: str, key: str) -> str:
        period_native = {
            ("advertising_monitor", "stats_7d"),
            ("advertising_optimizer", "stats_14d"),
            ("funnel", "funnel_7d"),
            ("search_positions", "positions"),
            ("finance", "report_7d"),
            ("cost_guard", "paid_storage"),
            ("cost_guard", "measurement_penalties"),
            ("cost_guard", "deductions"),
            ("cost_guard", "paid_acceptance"),
            ("documents", "documents_7d"),
            ("price_margin", "promotions"),
        }
        mixed = {
            ("inventory", "coverage"),
            ("advertising_optimizer", "deep_scan"),
        }
        filterable_exports = {
            ("finance", "worker_finance"),
        }
        if (agent, key) in period_native:
            return "selected_period"
        if (agent, key) in mixed:
            return "current_plus_period"
        if (agent, key) in filterable_exports:
            return "period_filterable_export"
        return "current_snapshot"

    async def _run_period_agent(self, name: str, period: PeriodContext) -> dict[str, Any]:
        if name == "supervisor":
            return {"agent": name, "status": "deferred_supervisor", "events": [], "actions": [], "snapshots": {}}
        token = set_analysis_period(period)
        started = datetime.now(timezone.utc).isoformat()
        try:
            result = await asyncio.wait_for(
                self.agents[name].run(),
                timeout=max(1.0, float(self.settings.agent_run_timeout_seconds)),
            )
        except Exception as exc:
            return {
                "agent": name,
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
                "events": [],
                "actions": [],
                "snapshots": {},
                "started_at": started,
                "finished_at": datetime.now(timezone.utc).isoformat(),
            }
        finally:
            reset_analysis_period(token)

        finished = datetime.now(timezone.utc).isoformat()
        snapshots: dict[str, Any] = {}
        for key, raw in result.snapshots:
            payload = dict(raw) if isinstance(raw, dict) else {"data": raw}
            payload["_analysis"] = {
                "mode": "period_audit",
                "scope": self._period_snapshot_scope(name, key),
                "period": period.to_dict(),
                "collected_at": finished,
            }
            db_key = f"period::{period.key}::{key}"
            self.db.save_snapshot(name, db_key, payload, finished)
            snapshots[key] = {"created_at": finished, "data": payload}

        events = []
        for idx, event in enumerate(result.events):
            row = asdict(event)
            row["event_key"] = row.get("key")
            row["id"] = f"period:{period.key}:{name}:{idx}"
            row["payload"] = dict(row.get("payload") or {})
            row["payload"]["_analysis"] = {
                "period": period.to_dict(),
                "mode": "period_audit",
                "scope": self._period_event_scope(name),
            }
            events.append(row)

        actions = []
        for proposal in result.actions:
            row = asdict(proposal)
            row["status"] = "period_read_only"
            actions.append(row)

        return {
            "agent": name,
            "status": "ok",
            "events": events,
            "actions": actions,
            "snapshots": snapshots,
            "started_at": started,
            "finished_at": finished,
        }

    def period_audit_status(self, period: PeriodContext) -> dict[str, Any]:
        item = self.db.latest_snapshot("period_audit", period.key)
        if not item:
            task = self._period_tasks.get(period.key)
            return {
                "status": "running" if task and not task.done() else "missing",
                "period": period.to_dict(),
            }
        data = dict(item.get("data") or {})
        data.setdefault("created_at", item.get("created_at"))
        task = self._period_tasks.get(period.key)
        if task and not task.done() and data.get("status") != "completed":
            data["status"] = "running"
        return data

    def start_period_audit(self, period: PeriodContext) -> dict[str, Any]:
        task = self._period_tasks.get(period.key)
        if task and not task.done():
            return {"status": "running", "period": period.to_dict()}
        task = asyncio.create_task(self.run_period_audit(period))
        self._period_tasks[period.key] = task
        def _cleanup(_task):
            if self._period_tasks.get(period.key) is _task:
                self._period_tasks.pop(period.key, None)
        task.add_done_callback(_cleanup)
        return {"status": "started", "period": period.to_dict()}

    async def run_period_audit(self, period: PeriodContext) -> dict[str, Any]:
        lock = self._period_audit_locks.setdefault(period.key, asyncio.Lock())
        if lock.locked():
            return self.period_audit_status(period)
        async with lock:
            started = datetime.now(timezone.utc).isoformat()
            self.db.save_snapshot(
                "period_audit",
                period.key,
                {"status": "running", "period": period.to_dict(), "started_at": started},
                started,
            )
            order = [
                "api_health", "cards", "advertising_monitor", "advertising_optimizer",
                "inventory", "supply", "funnel", "search_positions", "price_margin",
                "finance", "cost_guard", "reviews_questions", "buyer_chats", "orders_fbs",
                "returns_quality", "documents", "competitors", "experiments", "supervisor",
            ]
            agent_results: dict[str, Any] = {}
            period_snapshots: dict[str, Any] = {}
            period_events: list[dict[str, Any]] = []
            errors: list[dict[str, str]] = []
            try:
                for name in order:
                    if name == "supervisor":
                        continue
                    row = await self._run_period_agent(name, period)
                    agent_results[name] = {
                        "status": row.get("status"),
                        "started_at": row.get("started_at"),
                        "finished_at": row.get("finished_at"),
                        "error": row.get("error"),
                    }
                    if row.get("status") == "error":
                        errors.append({"agent": name, "error": str(row.get("error") or "")})
                    if row.get("snapshots"):
                        period_snapshots[name] = row["snapshots"]
                    period_events.extend(row.get("events") or [])
                    progress_at = datetime.now(timezone.utc).isoformat()
                    self.db.save_snapshot(
                        "period_audit",
                        period.key,
                        {
                            "status": "running",
                            "period": period.to_dict(),
                            "started_at": started,
                            "updated_at": progress_at,
                            "agents": agent_results,
                            "events": period_events,
                            "errors": errors,
                            "snapshot_groups": sorted(period_snapshots),
                        },
                        progress_at,
                    )
                    await asyncio.sleep(0.25)

                # Period snapshots describe period-dependent metrics, but
                # product identity, unit economics and current stock come from the
                # trusted live portfolio. Do not replace them with an empty shell:
                # that manufactured false "missing unit economics / nmID" blockers.
                portfolio = self.portfolio.snapshot()
                portfolio = dict(portfolio)
                portfolio["analysis_period"] = period.to_dict()
                portfolio["period"] = period.label
                ss = dict(period_snapshots)
                ss["_events"] = period_events
                ss["_operating_findings"] = []
                cards = self.decision_engine.build(portfolio, ss)
                cards, reviews = self.review_board.review(cards, portfolio, ss)

                period_relevant = [
                    e for e in period_events
                    if ((e.get("payload") or {}).get("_analysis") or {}).get("scope") != "current_snapshot"
                ]
                current_only = [
                    e for e in period_events
                    if ((e.get("payload") or {}).get("_analysis") or {}).get("scope") == "current_snapshot"
                ]
                critical = [e for e in period_relevant if e.get("severity") == "critical"]
                warning = [e for e in period_relevant if e.get("severity") == "warning"]
                digest_lines = [
                    f"Анализ периода {period.label}.",
                    f"Сигналы выбранного периода: критических {len(critical)}, предупреждений {len(warning)}; решений {len(cards)}.",
                    f"Дополнительно текущих сигналов вне исторического диапазона: {len(current_only)}.",
                ]
                for event in (critical + warning)[:8]:
                    digest_lines.append(f"• {event.get('title')}: {event.get('message')}")
                supervisor_event = {
                    "id": f"period:{period.key}:supervisor:0",
                    "agent": "supervisor",
                    "severity": "info",
                    "key": "period_supervisor_digest",
                    "event_key": "supervisor_digest",
                    "title": "Сводка управляющего за выбранный период",
                    "message": "\n".join(digest_lines),
                    "payload": {"period": period.to_dict(), "decision_count": len(cards)},
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }
                period_events.append(supervisor_event)
                agent_results["supervisor"] = {
                    "status": "ok",
                    "started_at": supervisor_event["created_at"],
                    "finished_at": supervisor_event["created_at"],
                    "error": None,
                }

                finished = datetime.now(timezone.utc).isoformat()
                payload = {
                    "status": "completed",
                    "period": period.to_dict(),
                    "started_at": started,
                    "finished_at": finished,
                    "agents": agent_results,
                    "events": period_events,
                    "decisions": [asdict(card) for card in cards],
                    "reviews": [asdict(review) for review in reviews],
                    "errors": errors,
                    "snapshot_groups": sorted(period_snapshots),
                }
                self.db.save_snapshot("period_audit", period.key, payload, finished)
                return payload
            except Exception as exc:
                finished = datetime.now(timezone.utc).isoformat()
                payload = {
                    "status": "error",
                    "period": period.to_dict(),
                    "started_at": started,
                    "finished_at": finished,
                    "error": f"{type(exc).__name__}: {exc}",
                    "agents": agent_results,
                    "events": period_events,
                    "errors": errors,
                }
                self.db.save_snapshot("period_audit", period.key, payload, finished)
                return payload

    async def execute_action(self, action_id: int, approved_by: str = "user") -> dict[str, Any]:
        lock=self._action_locks.setdefault(action_id, asyncio.Lock())
        async with lock:
            return await self._execute_action_locked(action_id, approved_by)

    async def _execute_action_locked(self, action_id: int, approved_by: str = "user") -> dict[str, Any]:
        action = self.db.get_action(action_id)
        if not action:
            raise KeyError(f"action {action_id} not found")
        if action["status"] not in {"pending", "approved"}:
            return {"id": action_id, "status": action["status"]}
        proposal = ActionProposal(
            agent=action["agent"],
            tool=action["tool"],
            arguments=action["arguments"],
            reason=action["reason"],
            risk=action["risk"],
            status=action["status"],
            id=action_id,
            created_at=action["created_at"],
        )
        ok, why = self.policy_engine.validate_action(proposal)
        if not ok:
            self.db.update_action(action_id, "blocked", error=why)
            return {"id": action_id, "status": "blocked", "reason": why}
        allowed, why_exec = self.policy_engine.execution_allowed()
        if not allowed:
            self.db.update_action(action_id, "simulated", result={"reason": why_exec, "tool": proposal.tool, "arguments": proposal.arguments})
            ev = Event(
                agent=proposal.agent,
                severity="info",
                key=f"action_simulated:{action_id}",
                title=f"Действие #{action_id} симулировано",
                message=f"{proposal.tool}. {why_exec}. {proposal.reason}",
                payload={"tool": proposal.tool, "arguments": proposal.arguments},
            )
            self.db.save_event(ev)
            return {"id": action_id, "status": "simulated", "reason": why_exec}
        self.db.update_action(action_id, "approved")
        try:
            args = {k: v for k, v in proposal.arguments.items() if not k.startswith("_")}
            result = await self.wb.call(proposal.tool, args)
            self.db.update_action(action_id, "executed", result=result)
            ev = Event(
                agent=proposal.agent,
                severity="info",
                key=f"action_executed:{action_id}",
                title=f"Действие #{action_id} выполнено",
                message=f"{proposal.tool}. Подтверждение: {approved_by}. {proposal.reason}",
                payload={"tool": proposal.tool, "arguments": args, "result": result},
            )
            self.db.save_event(ev)
            return {"id": action_id, "status": "executed", "result": result}
        except Exception as e:
            self.db.update_action(action_id, "error", error=str(e))
            return {"id": action_id, "status": "error", "error": str(e)}

    async def reject_action(self, action_id: int) -> dict[str, Any]:
        action = self.db.get_action(action_id)
        if not action:
            raise KeyError(action_id)
        if action["status"] != "pending":
            return {"id": action_id, "status": action["status"]}
        self.db.update_action(action_id, "rejected")
        return {"id": action_id, "status": "rejected"}

    async def approve_action_text(self, action_id: int) -> str:
        r = await self.execute_action(action_id, approved_by="telegram")
        return f"Действие #{action_id}: {r.get('status')}" + (f"\n{r.get('error')}" if r.get("error") else "")

    async def reject_action_text(self, action_id: int) -> str:
        r = await self.reject_action(action_id)
        return f"Действие #{action_id}: {r.get('status')}"

    async def status_text(self) -> str:
        pending = self.db.pending_actions(limit=100)
        events = self.db.recent_events(hours=24, limit=200)
        critical = sum(1 for e in events if e.get("severity") == "critical")
        warning = sum(1 for e in events if e.get("severity") == "warning")
        return f"WB AI Control Center\nКритические события 24ч: {critical}\nПредупреждения 24ч: {warning}\nОжидают подтверждения: {len(pending)}"
