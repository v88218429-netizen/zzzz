#!/usr/bin/env python3
"""Offline contract tests. No Google, GitHub or YouTube calls."""
import json
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch
import ai_video_monitor_autonomous as vm

TZ = ZoneInfo("Europe/Moscow")


def row(task="AI_BUSINESS", freq="Ежедневно", days="Каждый день", last="2026-09-25 07:04"):
    r = [""] * 25
    r[0] = task
    r[1] = "TRUE"
    r[2] = "AI / бизнес"
    r[3] = "AI автоматизация Wildberries 2026"
    r[6] = freq
    r[7] = days
    r[8] = "07:00"
    r[9] = "6"
    r[12] = "Высокий"
    r[15] = "AI_BUSINESS"
    r[16] = last
    return r


class MonitorTests(unittest.TestCase):
    def test_daily_due_once(self):
        moment = datetime(2026, 10, 14, 15, 0, tzinfo=TZ)
        self.assertTrue(vm.due(row(), moment))
        self.assertFalse(vm.due(row(last="2026-10-14 09:00"), moment))
        self.assertFalse(vm.due(row(last="2026-09-25"), moment.replace(hour=6)))

    def test_weekdays(self):
        moment = datetime(2026, 10, 14, 15, 0, tzinfo=TZ)
        self.assertTrue(vm.due(row(freq="Пн/Ср/Пт", days="Пн/Ср/Пт"), moment))
        self.assertFalse(vm.due(row(freq="Еженедельно", days="Сб"), moment))

    def test_task_inactive(self):
        task = row()
        task[1] = False
        self.assertFalse(vm.due(task, datetime(2026, 10, 14, 15, tzinfo=TZ)))

    def test_atomic_values_payload(self):
        sh = object.__new__(vm.Sheets)
        seen = {}
        def fake(method, url, **kwargs):
            seen.update(kwargs.get("json", {}))
        sh.req = fake
        sh.batch([("Задания", "Q2", "2026-10-14 09:00"),
                  ("Задания", "T2", 2),
                  ("История", "A20:C20", ["run", "AI_BUSINESS", "2026-10-14"])])
        self.assertEqual(seen["data"][0]["values"], [["2026-10-14 09:00"]])
        self.assertEqual(seen["data"][1]["values"], [[2]])
        self.assertEqual(seen["data"][2]["values"], [["run", "AI_BUSINESS", "2026-10-14"]])

    def test_grounded_ai_accepts_only_evidence(self):
        transcript = "[0:00] У нас есть конкретный способ: сначала считайте затраты на замену владельца."
        data = {"ideas": [
            {"goal_code": "BUSINESS", "idea": "Считать расходы на замену владельца по каждому проекту",
             "why_for_owner": "Это связано с автономным бизнесом",
             "action": "Вынести часы владельца и стоимость замены в табличный расчет",
             "metric": "Свободная прибыль", "fact": "Сравнивать стоимость замены",
             "evidence": "сначала считайте затраты на замену владельца", "confidence": "TEST"},
            {"goal_code": "BUSINESS", "idea": "Придуманный вывод без подтверждающей цитаты",
             "action": "Сразу потратить деньги на непроверенный товар",
             "evidence": "В видео этого вообще никогда не было", "confidence": "TEST"},
        ]}
        class Resp:
            ok = True
            def json(self): raise ValueError("plaintext")
            text = json.dumps(data, ensure_ascii=False)
        goals = [["Код", "Направление", "Цель"], ["BUSINESS", "Бизнес", "Автономность"]]
        with patch.dict(vm.os.environ, {"GITHUB_TOKEN": "unit-test-nonsecret"}), patch.object(vm.SESSION, "post", return_value=Resp()):
            ideas = vm.model_json(row(), {"title": "Бизнес"}, transcript, goals, [["№", "Правило"]])
        self.assertEqual(len(ideas), 1)
        self.assertEqual(ideas[0]["goal_code"], "BUSINESS")


if __name__ == "__main__":
    unittest.main()
