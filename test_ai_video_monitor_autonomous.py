#!/usr/bin/env python3
"""Offline contract tests. No Google, GitHub or YouTube calls."""
import json
import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
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

    def test_existing_transcript_is_used_before_network(self):
        with TemporaryDirectory() as temporary:
            original = vm.SOURCE_DIR
            try:
                vm.SOURCE_DIR = Path(temporary)
                (vm.SOURCE_DIR / "20261014_test_QhKuo6meaTs.md").write_text(
                    "# Title\n\n## Transcript\n\n"
                    "[00:12] Эффективная аналитика начинается с проверяемого действия. " * 7,
                    encoding="utf-8")
                with patch.object(vm, "youtube_transcript_api",
                                  side_effect=AssertionError("Network should not be called")):
                    content, method = vm.transcript_for("QhKuo6meaTs")
                self.assertIn("проверяемого", content)
                self.assertIn("cache", method)
            finally:
                vm.SOURCE_DIR = original

    def test_used_count_ignores_formatted_trailing_rows(self):
        rows = [["ID", "Task"], ["A", "Active"], ["", "formula-like tail"],
                ["", ""], []]
        self.assertEqual(vm.used_count(rows), 2)

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

    def test_ai_evidence_index_maps_to_real_transcript_quote(self):
        transcript = ("[0:03] Сначала проверяйте устойчивый спрос на маленькой партии "
                      "и сравнивайте прибыль с полными затратами на логистику, налог и рекламу.")
        payload = {"ideas": [{
            "goal_code": "BUSINESS",
            "idea": "Проверять новый товар маленькой партией до крупных закупок",
            "why_for_owner": "Нужно уменьшить кассовый риск WB",
            "action": "Запустить тест из 10 изделий и записывать фактическую маржу",
            "metric": "Чистая прибыль на единицу после расходов",
            "fact": "Рекомендован поэтапный запуск", "evidence_index": 0, "confidence": "TEST"}]}
        class Response:
            ok = True
            def json(self):
                return {"message": {"content": json.dumps(payload, ensure_ascii=False)}}
        goals = [["Код", "Направление", "Цель"],
                 ["BUSINESS", "Бизнес", "Автономный прибыльный бизнес"]]
        with patch.object(vm.SESSION, "post", return_value=Response()):
            ideas = vm.model_json(row(), {"title": "Бизнес"}, transcript,
                                  goals, [["№", "Правило"]])
        self.assertEqual(len(ideas), 1)
        self.assertIn("Сначала проверяйте", ideas[0]["evidence"])

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
            def json(self): return {"message": {"content": json.dumps(data, ensure_ascii=False)}}
        goals = [["Код", "Направление", "Цель"], ["BUSINESS", "Бизнес", "Автономность"]]
        with patch.dict(vm.os.environ, {"GITHUB_TOKEN": "unit-test-nonsecret"}), patch.object(vm.SESSION, "post", return_value=Resp()):
            ideas = vm.model_json(row(), {"title": "Бизнес"}, transcript, goals, [["№", "Правило"]])
        self.assertEqual(len(ideas), 1)
        self.assertEqual(ideas[0]["goal_code"], "BUSINESS")

    def test_ai_claim_without_ai_evidence_is_rejected(self):
        transcript = ("[0:02] Автор показывает CPC-рекламу: сравнивает поисковые ставки, "
                      "количество показов и расходы в кабинете продавца, "
                      "не показывая алгоритм прогнозирования или его проверку.")
        # The selected citation discusses advertising, not an AI forecasting mechanism.
        payload = {"ideas": [{
            "goal_code": "BUSINESS",
            "idea": "Внедрить AI для точного прогнозирования рекламных ставок WB",
            "why_for_owner": "Управлять рекламой без ручной работы",
            "action": "Создать AI-инициативу для автоматизации рекламной кампании",
            "metric": "Повышение эффективности",
            "fact": "AI предсказывает рекламные результаты", "evidence_index": 0,
            "confidence": "TEST"}]}
        class Resp:
            ok = True
            def json(self):
                return {"message": {"content": json.dumps(payload, ensure_ascii=False)}}
        goals = [["Код", "Направление", "Цель"], ["BUSINESS", "Бизнес", "Автономность"]]
        with patch.object(vm.SESSION, "post", return_value=Resp()):
            ideas = vm.model_json(row(), {"title": "CPC-реклама"}, transcript,
                                  goals, [["№", "Правило"]])
        self.assertEqual(ideas, [])



if __name__ == "__main__":
    unittest.main()
