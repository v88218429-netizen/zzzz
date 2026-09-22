import asyncio
import unittest

import server


def result(position, status="OK"):
    return {
        "position": position,
        "status": status if position is not None else "NOT_FOUND_TOP_100",
        "endpoint": "fake",
        "http_status": 200,
        "response_ms": 10,
        "checked_depth": 20,
    }


class FakeOzon:
    def __init__(self, values):
        self.values = iter(values)

    def position(self, query, sku, max_position):
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return result(value)


class RadarBehaviorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.old_ozon = server.ozon
        self.old_confirm = server.CONFIRM_SECONDS
        self.old_token = server.TELEGRAM_BOT_TOKEN
        self.old_chat = server.TELEGRAM_CHAT_ID
        server.runtime = server.Runtime()
        server.CONFIRM_SECONDS = 0
        server.TELEGRAM_BOT_TOKEN = ""
        server.TELEGRAM_CHAT_ID = ""

    def tearDown(self):
        server.ozon = self.old_ozon
        server.CONFIRM_SECONDS = self.old_confirm
        server.TELEGRAM_BOT_TOKEN = self.old_token
        server.TELEGRAM_CHAT_ID = self.old_chat

    def task(self):
        return server.RadarTaskIn(
            article="test",
            sku="5094364543",
            query="лопата садовая",
            interval_min=1,
            drop_threshold=3,
            top_boundary=10,
            max_position=100,
        )

    async def test_drop_is_confirmed_and_not_repeated_every_tick(self):
        task = self.task()
        state = server.TaskState(task=task, last_position=7)
        server.ozon = FakeOzon([12, 12, 13, 16, 16])

        await server.check_one("k", state)
        await server.check_one("k", state)
        await server.check_one("k", state)

        checks = [e for e in server.runtime.events if e["kind"] == "CHECK"]
        self.assertEqual([e["position"] for e in checks], [12, 13, 16])
        self.assertEqual([e["alert_type"] for e in checks], ["OUT_TOP", "", "DROP"])
        self.assertEqual([e["delta"] for e in checks], [-5, -1, -3])

    async def test_miss_requires_confirmation_before_outside_top(self):
        task = self.task()
        state = server.TaskState(task=task, last_position=8)
        server.ozon = FakeOzon([None, None])

        await server.check_one("k", state)

        check = [e for e in server.runtime.events if e["kind"] == "CHECK"][-1]
        self.assertEqual(check["position"], 101)
        self.assertEqual(check["position_text"], ">100")
        self.assertEqual(check["status"], "OUTSIDE_TOP_100_CONFIRMED")
        self.assertEqual(check["alert_type"], "OUT_TOP")

    async def test_single_miss_then_found_is_not_false_drop(self):
        task = self.task()
        state = server.TaskState(task=task, last_position=8)
        server.ozon = FakeOzon([None, 9])

        await server.check_one("k", state)

        check = [e for e in server.runtime.events if e["kind"] == "CHECK"][-1]
        self.assertEqual(check["position"], 9)
        self.assertEqual(check["delta"], -1)
        self.assertEqual(check["alert_type"], "")

    async def test_top_recovery_resets_alert(self):
        task = self.task()
        state = server.TaskState(
            task=task,
            last_position=12,
            alert_active=True,
            alert_kind="OUT_TOP",
            alert_origin_position=8,
            last_alert_position=12,
        )
        server.ozon = FakeOzon([8])

        await server.check_one("k", state)

        check = [e for e in server.runtime.events if e["kind"] == "CHECK"][-1]
        self.assertEqual(check["alert_type"], "RECOVERY")
        self.assertFalse(state.alert_active)

    async def test_source_error_never_becomes_position(self):
        task = self.task()
        state = server.TaskState(task=task, last_position=7)
        server.ozon = FakeOzon([RuntimeError("HTTP 403")])

        await server.check_one("k", state)

        self.assertEqual(state.last_position, 7)
        event = list(server.runtime.events)[-1]
        self.assertEqual(event["kind"], "ERROR")
        self.assertIsNone(event["position"])


if __name__ == "__main__":
    unittest.main()
