#!/usr/bin/env python3
"""Grounded backfill of already verified archived transcripts into the live Sheet.

One cloud-runner invocation; no Mac, Make, Railway, model API keys or
external transcript websites are required. Idempotent by original video ID.
"""
import argparse
import re
from datetime import datetime
from pathlib import Path

from ai_video_monitor_autonomous import (
    Sheets, SOURCE_DIR, TZ, cell, model_json, result_rows,
    transcript_for, used_count, valid_id,
)


def sheet_source_id(value):
    match = re.search(r"(?:v=|youtu\.be/)([\w-]{11})", str(value))
    return match.group(1) if match else ""


def available_video_ids():
    ids = set()
    for path in SOURCE_DIR.glob("*.md"):
        match = re.search(r"_([\w-]{11})\.md$", path.name)
        if match:
            ids.add(match.group(1))
    return ids


def run(max_videos, target):
    sh = Sheets()
    videos = sh.get("Видео", "A1:O1000")
    knowledge = sh.get("База знаний", "A1:O1000")
    owner_ideas = sh.get("Идеи для владельца", "A1:J200")
    goals = sh.get("Мои цели", "A1:G100")
    rules = sh.get("Правила анализа", "A1:C100")
    history = sh.get("История", "A1:K1000")
    used_knowledge = {str(cell(r, 4)) for r in knowledge[1:] if cell(r, 4)}
    used_ideas = {sheet_source_id(cell(r, 7)) for r in owner_ideas[1:] if cell(r, 7)}
    cached = available_video_ids()
    if target and target not in cached:
        raise RuntimeError("Selected video has no archived verified transcript")

    candidates = []
    for idx, row in enumerate(videos[1:], start=2):
        vid = str(cell(row, 0))
        if (not vid or vid not in cached or vid in used_knowledge or vid in used_ideas
                or (target and vid != target)):
            continue
        status = str(cell(row, 10))
        if status not in ("COMPLETE_VERIFIED", "TRANSCRIPT_VERIFIED", "Обработано"):
            continue
        candidates.append((idx, row))
    # Prefer detailed commercial case studies over generic videos.
    def quality(entry):
        title = str(cell(entry[1], 4)).lower()
        best = sum(1 for word in ("бизнес", "business", "кейс", "автомат", "прибыл",
                                   "доход", "контент", "продаж", "систем") if word in title)
        return (-best, entry[0])

    candidates.sort(key=quality)
    if target and not candidates:
        print("BACKFILL_SKIP_DUPLICATE video="+target)
        return
    print(f"BACKFILL_AVAILABLE={len(candidates)} cached_ids={len(cached)}")
    if not candidates:
        return
    now = datetime.now(TZ)
    for rowindex, video_row in candidates[:max_videos]:
        vid = str(cell(video_row, 0))
        day = now.strftime("%Y-%m-%d")
        task = [""] * 25
        task[0] = str(cell(video_row, 1))
        task[2] = "Предпринимательство / систематизация / AI"
        task[14] = "Реальный проверяемый бизнес-кейс, применимые действия, числа"
        task[15] = "OWNER_IDEAS"
        video = {
            "id": vid, "title": str(cell(video_row, 4)),
            "channel": str(cell(video_row, 5)),
            "duration": None,
            "published": str(cell(video_row, 7)),
        }
        transcript, provider = transcript_for(vid)
        if len(transcript) < 250:
            raise RuntimeError("Short verified transcript: "+vid)
        source_file = next(SOURCE_DIR.glob("*_"+vid+".md"))
        transcript_url = ("https://github.com/v88218429-netizen/zzzz/blob/mainggg/"
                          "transcripts/"+source_file.name)
        error = ""
        try:
            ideas = model_json(task, video, transcript, goals, rules)
        except Exception as exc:
            ideas = []
            error = type(exc).__name__
        _, kb, proposal_rows = result_rows(task, video, ideas, transcript_url, day)
        if error:
            status, novelty = "AI_RETRY_PENDING", "TRANSCRIPT_VERIFIED"
        else:
            status, novelty = "Обработано AI", ("NEW SIGNAL" if ideas else "IGNORE")
        blocks = [
            ("Видео", f"J{rowindex}", transcript_url),
            ("Видео", f"K{rowindex}:O{rowindex}",
                [status, novelty, ideas[0]["fact"] if ideas else "",
                 ideas[0]["action"] if ideas else "", task[15]]),
        ]
        def append(name, records, current):
            for offset, record in enumerate(records):
                col = chr(ord("A") + len(record) - 1)
                start = current + offset + 1
                blocks.append((name, f"A{start}:{col}{start}", record))

        append("База знаний", kb, used_count(knowledge))
        append("Идеи для владельца", proposal_rows, used_count(owner_ideas))
        runid = f"ARCHIVE_AI_{valid_id(vid)}_{now.strftime('%Y%m%d_%H%M%S')}"
        logrow = [runid, task[0], now.strftime("%Y-%m-%d %H:%M"),
                  now.strftime("%Y-%m-%d %H:%M"), "GitHub verified transcript cache",
                  0, 0, error, "Ошибка" if error else "Готово",
                  f"Архивная транскрипция {vid}, проверенных идей {len(proposal_rows)}",
                  "GitHub Actions ai-video-monitor-backfill.yml"]
        append("История", [logrow], used_count(history))
        sh.batch(blocks)
        history.append(logrow)
        knowledge.extend(kb)
        owner_ideas.extend(proposal_rows)
        # Re-read exact written locations: optimistic success is not acceptance.
        verified_video = sh.get("Видео", f"J{rowindex}:O{rowindex}")
        if not verified_video or str(verified_video[0][1]) != status:
            raise RuntimeError("No verified sheet status after backfill")
        if proposal_rows:
            bottom = used_count(owner_ideas)
            verified = sh.get("Идеи для владельца", f"A{bottom}:J{bottom}")
            if not verified or verified[0][6] != proposal_rows[-1][6]:
                raise RuntimeError("Owner idea missing after Sheets write")
        print(f"BACKFILL_COMMITTED video={vid} words={len(transcript.split())} "
              f"ideas={len(proposal_rows)} model_error={error or 'none'}")
        if proposal_rows:
            print("BACKFILL_FIRST_ID="+proposal_rows[0][6])


if __name__ == "__main__":
    cli = argparse.ArgumentParser()
    cli.add_argument("--video-id", default="")
    cli.add_argument("--max-videos", type=int, default=1)
    args = cli.parse_args()
    run(max(1, min(args.max_videos, 3)), args.video_id)
