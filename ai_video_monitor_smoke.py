#!/usr/bin/env python3
"""Real but non-mutating cloud smoke for search, transcripts and GitHub AI."""
from pathlib import Path
import sys
import ai_video_monitor_autonomous as monitor

def main():
    print("SMOKE_PHASE=search")
    try:
        videos = monitor.search_youtube("реклама Wildberries 2026", 8)
        print("SEARCH_REAL=OK", "count="+str(len(videos)))
    except Exception as exc:
        print("SEARCH_REAL=FAIL",type(exc).__name__, str(exc)[:100])

    print("SMOKE_PHASE=transcript")
    vid = "oNBF8G_md28"
    try:
        transcript, method = monitor.transcript_for(vid)
        print("TRANSCRIPT_REAL=OK", "chars="+str(len(transcript)), "source="+method)
    except Exception as exc:
        print("TRANSCRIPT_REAL=FAIL",type(exc).__name__, str(exc)[:100])

    print("SMOKE_PHASE=ai")
    path = Path("transcripts/PETR_OSIPOV_NEWEST_15_20261010_07_oNBF8G_md28.md")
    data = path.read_text(encoding="utf-8")
    example = {"title": "How to FIND YOUR CALLING that brings both money and joy"}
    task = [""] * 25
    task[2],task[14],task[15] = "Бизнес", "Бизнес-модели и ограничения владельца", "BUSINESS"
    goals = [
        ["Код","Направление","Цель","Что уточнить"],
        ["BUSINESS","Бизнес","Масштабируемое дело без владельца","Как выбрать нишу"],
        ["INCOME","Доход","Устойчивый доход","Какая модель экономически реальна"],
    ]
    try:
        ideas = monitor.model_json(task,example,data,goals,[["№","Правило"]])
        print("AI_REAL=OK","grounded_ideas="+str(len(ideas)))
    except Exception as exc:
        print("AI_REAL=FAIL",type(exc).__name__,str(exc)[:110])
        raise

if __name__ == "__main__":
    main()
