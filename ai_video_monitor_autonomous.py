#!/usr/bin/env python3
"""Autonomous AI Video Monitor: Sheets -> YouTube -> transcripts -> grounded AI -> Sheets.

Cloud only: intended for GitHub Actions. Never prints credentials, transcripts or
personal goal descriptions. Google credentials are supplied as Actions secrets.
"""
import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import random
import re
import subprocess
import tempfile
import time
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2.credentials import Credentials

ROOT = Path(__file__).resolve().parent
SHEET_ID = "1ejXreSQYBDDlTDI9ZtRs7hW0vgdssVRm__hNtxJmP64"
TZ = ZoneInfo("Europe/Moscow")
REPO = "v88218429-netizen/zzzz"
SOURCE_DIR = ROOT / "transcripts"
SOURCE_DIR.mkdir(exist_ok=True)
TIMEOUT = 40
SESSION = requests.Session()


def now_msk():
    return datetime.now(TZ)


def fail(message):
    raise RuntimeError(message)


class Sheets:
    def __init__(self):
        raw = os.environ.get("CLASPRC_JSON", "")
        if not raw:
            fail("CLASPRC_JSON is absent; Sheets automation blocked")
        deploy = ROOT / "google_apps_script_ai_monitor" / "bridge_deployment.json"
        self.bridge_url = ""
        if deploy.exists():
            from ai_video_bridge_auth import bridge_token
            self.bridge_url = json.loads(deploy.read_text())["url"]
            self.bridge_key = bridge_token()
            return
        cfg = json.loads(raw)["tokens"]["default"]
        credentials = Credentials(
            token=cfg.get("access_token"),
            refresh_token=cfg.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=cfg.get("client_id"),
            client_secret=cfg.get("client_secret"),
        )
        credentials.refresh(GoogleRequest())
        self.headers = {"Authorization": "Bearer " + credentials.token}
        self.root = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}"

    def req(self, method, path, **kwargs):
        response = SESSION.request(method, self.root + path, headers=self.headers, timeout=TIMEOUT, **kwargs)
        if not response.ok:
            try:
                error = response.json().get("error", {})
                reason = error.get("status", "unknown")
            except Exception:
                reason = "unknown"
            fail(f"Sheets API {response.status_code} ({reason})")
        return response.json()

    def bridge(self, payload):
        try:
            response = SESSION.post(self.bridge_url,
                json={"token": self.bridge_key, **payload}, timeout=65)
        except requests.RequestException as exc:
            fail("Apps Script transport: " + type(exc).__name__)
        if response.status_code != 200:
            from urllib.parse import urlsplit
            host = urlsplit(response.url).hostname
            fail("Apps Script bridge HTTP " + str(response.status_code) + " host=" + str(host) + " redirects=" + str(len(response.history)) + " type=" + str(response.headers.get("content-type", "")))
        try:
            response_json = response.json()
        except ValueError:
            fail("Apps Script bridge returned HTML; check deployment access")
        if not response_json.get("ok"):
            fail("Apps Script bridge: " + str(response_json.get("error", "unknown"))[:120])
        return response_json

    def get(self, name, end="A1:Z1000"):
        a1 = f"'{name}'!{end}"
        if getattr(self, "bridge_url", ""):
            return self.bridge({"op": "read", "ranges": [a1]})["values"][0]
        return self.req("GET", "/values/" + quote(a1, safe="")).get("values", [])

    def batch(self, blocks):
        if not blocks:
            return
        data = [{"range": f"'{name}'!{cell}", "values": [
            values if isinstance(values, list) else [values]]}
            for name, cell, values in blocks]
        if getattr(self, "bridge_url", ""):
            self.bridge({"op": "write", "data": data})
            return
        self.req("POST", "/values:batchUpdate", json={
            "valueInputOption": "RAW", "data": data,
        })


def cell(row, index):
    return row[index] if index < len(row) else ""


def active(row):
    return str(cell(row, 1)).strip().lower() in ("true", "1", "yes", "да")


def parse_dt(s):
    try:
        return datetime.fromisoformat(str(s).replace(" ", "T")).replace(tzinfo=TZ)
    except (ValueError, TypeError):
        return None


WEEK = {"пн": 0, "вт": 1, "ср": 2, "чт": 3, "пт": 4, "сб": 5, "вс": 6}


def weekdays(row):
    freq = str(cell(row, 6)).strip().lower()
    label = str(cell(row, 7)).lower()
    if "ежедневно" in freq or "каждый день" in label:
        return set(range(7))
    days = {n for name, n in WEEK.items() if name in label or name in freq}
    if days:
        return days
    if "еженедельно" in freq:
        return {0}
    return set()


def due(row, now, force=False):
    if not active(row) or not str(cell(row, 0)).strip():
        return False
    if force:
        return True
    if now.weekday() not in weekdays(row):
        return False
    try:
        hh, mm = map(int, str(cell(row, 8)).split(":")[:2])
    except ValueError:
        return False
    if now.hour * 60 + now.minute < hh * 60 + mm:
        return False
    last = parse_dt(cell(row, 16))
    return not (last and last.date() == now.date())


def next_run(row, start):
    allowed = weekdays(row)
    if not allowed:
        return ""
    try:
        hh, mm = map(int, str(cell(row, 8)).split(":")[:2])
    except ValueError:
        return ""
    for delta in range(1, 9):
        day = start + timedelta(days=delta)
        if day.weekday() in allowed:
            return day.replace(hour=hh, minute=mm, second=0).strftime("%Y-%m-%d %H:%M")
    return ""


def search_youtube(query, count=14):
    errors = []
    for prefix in ("ytsearchdate", "ytsearch"):
        cmd = ["yt-dlp", "--flat-playlist", "--dump-single-json",
               "--no-warnings", "--ignore-errors",
               f"{prefix}{min(count, 25)}:{query}"]
        try:
            response = subprocess.run(cmd, capture_output=True, text=True, timeout=80)
            if response.returncode:
                errors.append("yt-dlp exit " + str(response.returncode))
                continue
            entries = json.loads(response.stdout).get("entries") or []
            found = []
            for data in entries:
                if not data or not re.fullmatch(r"[\w-]{11}", str(data.get("id", ""))):
                    continue
                found.append({
                    "id": data["id"],
                    "title": data.get("title") or data["id"],
                    "channel": data.get("channel") or data.get("uploader") or "",
                    "duration": data.get("duration"),
                    "published": data.get("upload_date") or "",
                })
            if found:
                return found
        except (ValueError, subprocess.TimeoutExpired) as exc:
            errors.append(type(exc).__name__)
    fail("YouTube search unavailable: " + "; ".join(errors[:3]))


def clean_vtt(raw):
    raw = re.sub(r"<[^>]+>", "", raw)
    raw = html.unescape(raw)
    lines = []
    stamp = None
    old = ""
    for line in raw.splitlines():
        line = line.strip()
        mt = re.match(r"(?:(\d\d):)?(\d\d):(\d\d)[\.,]\d+ -->", line)
        if mt:
            hours, mins, secs = mt.groups()
            stamp = f"{hours}:{mins}:{secs}" if hours else f"{mins}:{secs}"
            continue
        if not line or line.startswith(("WEBVTT", "Kind:", "Language:", "NOTE")) or "-->" in line or re.match(r"^\d+$", line):
            continue
        line = re.sub(r"\s+", " ", line).strip()
        if line != old and stamp:
            lines.append(f"[{stamp}] {line}")
            old = line
    return "\n".join(lines)


def ytdlp_captions(vid):
    url = "https://www.youtube.com/watch?v=" + vid
    with tempfile.TemporaryDirectory() as directory:
        output = str(Path(directory) / "%(id)s")
        cmd = ["yt-dlp", "--skip-download", "--no-playlist",
               "--write-subs", "--write-auto-subs",
               "--sub-langs", "ru,en", "--sub-format", "vtt",
               "--no-warnings", "-o", output, url]
        result = subprocess.run(cmd, text=True, capture_output=True, timeout=90)
        candidates = list(Path(directory).glob("*.vtt"))
        for path in sorted(candidates, key=lambda p: "ru" not in p.name):
            content = clean_vtt(path.read_text(encoding="utf-8", errors="replace"))
            if len(content) > 250:
                return content, "yt-dlp captions"
        fail("yt-dlp captions missing (exit " + str(result.returncode) + ")")


def youtube_transcript_api(vid):
    from youtube_transcript_api import YouTubeTranscriptApi
    fetched = YouTubeTranscriptApi().fetch(vid, languages=["ru", "en"])
    lines = []
    for item in fetched:
        seconds = int(item.start)
        stamp = f"{seconds // 60}:{seconds % 60:02d}"
        text = re.sub(r"\s+", " ", str(item.text)).strip()
        if text:
            lines.append(f"[{stamp}] {text}")
    result = "\n".join(lines)
    if len(result) < 250:
        fail("youtube-transcript-api empty")
    return result, "youtube-transcript-api"


def youtube_transcript_ai(vid):
    for url in (f"https://youtube-transcript.ai/transcript/{vid}.txt?lang=ru",
                f"https://youtube-transcript.ai/transcript/{vid}.txt"):
        response = SESSION.get(url, timeout=40)
        if response.status_code != 200:
            continue
        raw = response.text
        if any(s in raw.lower() for s in ("high volume", "higher rate limits", "email will@", "<html")):
            continue
        m = re.search(r"(?m)^\[\d{1,2}:\d{2}(?::\d{2})?\]", raw)
        if m and len(raw[m.start():]) >= 250:
            return raw[m.start():].strip(), "youtube-transcript.ai"
    fail("youtube-transcript.ai unavailable")


def free_transcript_api(vid):
    response = SESSION.get("https://api.freetranscriptapi.com/v1/transcript",
                           params={"video_url": vid, "lang": "ru"}, timeout=35)
    if not response.ok:
        fail("FreeTranscriptAPI HTTP " + str(response.status_code))
    data = response.json()
    segments = data.get("transcript") or data.get("segments") or []
    parts = []
    for item in segments:
        seconds = int(float(item.get("start", 0) or 0))
        body = re.sub(r"\s+", " ", str(item.get("text", ""))).strip()
        if body:
            parts.append(f"[{seconds // 60}:{seconds % 60:02d}] {body}")
    text = "\n".join(parts)
    if len(text) < 250:
        fail("FreeTranscriptAPI empty")
    return text, "FreeTranscriptAPI"


def transcript_for(vid):
    err = []
    for attempt in range(2):
        for provider in (youtube_transcript_api, youtube_transcript_ai, free_transcript_api, ytdlp_captions):
            try:
                return provider(vid)
            except Exception as exc:
                err.append(provider.__name__ + ":" + type(exc).__name__)
        if attempt == 0:
            time.sleep(3 + random.random() * 3)
    fail("transcript retry pending: " + ", ".join(err[-3:]))


def model_json(task, video, transcript, goals, rules):
    filtered_goals = [
        {"code": cell(g, 0), "area": cell(g, 1), "outcome": cell(g, 2),
         "questions": cell(g, 3)}
        for g in goals[1:] if cell(g, 0)
    ]
    instruction = (
        "Ты аналитик владельца бизнеса. Общайся на русском. Не пересказывай видео."
        " Цель — выделить не больше 2 НОВЫХ и конкретных применимых идей,"
        " напрямую привязанных к целям владельца. Если нет нового, верни пустой список."
        " Не выдумывай цифры, ссылки и факты. Утверждения автора помечай как гипотезы."
        " Верни СТРОГО JSON: {\"ideas\":[{\"goal_code\":\"BUSINESS\","
        "\"idea\":\"...\", \"why_for_owner\":\"...\", \"action\":\"...\","
        "\"metric\":\"...\", \"fact\":\"...\", \"evidence\":\"...\","
        "\"confidence\":\"TEST\"}]}."
        " Evidence — ДОСЛОВНАЯ непрерывная цитата длиной 25–150 символов"
        " из предоставленного транскрипта; без неё идея недействительна."
        " goal_code должен точно совпасть с одним кодом из целей."
    )
    messages = [{"role": "system", "content": instruction},
                {"role": "user", "content": json.dumps({
                    "task": {"area": cell(task, 2), "extract": cell(task, 14),
                             "tag": cell(task, 15)},
                    "video": video["title"],
                    "goals": filtered_goals,
                    "rules": [cell(r, 1) for r in rules[1:12] if cell(r, 1)],
                    "transcript": transcript[:12000],
                }, ensure_ascii=False)}]
    # GitHub Models was retired in July 2026. Local CPU model needs no API key
    # and runs entirely within the cloud job; content is never sent to unknown APIs.
    response = SESSION.post("http://127.0.0.1:11434/api/chat", json={
        "model": os.environ.get("VIDEO_LOCAL_MODEL", "qwen2.5:1.5b"),
        "messages": messages, "stream": False, "format": "json",
        "options": {"temperature": 0.1, "num_predict": 850, "num_ctx": 8192},
    }, timeout=300)
    if not response.ok:
        fail("Local AI HTTP " + str(response.status_code))
    output = response.json().get("message", {}).get("content", "")
    match = re.search(r"\{[\s\S]*\}", str(output))
    if not match:
        fail("AI returned no JSON")
    data = json.loads(match.group())
    allowed = {cell(g, 0) for g in goals[1:] if cell(g, 0)}
    clean = []
    t_norm = re.sub(r"\s+", " ", transcript).lower()
    for idea in data.get("ideas", [])[:2]:
        evidence = re.sub(r"\s+", " ", str(idea.get("evidence", ""))).strip()
        if (idea.get("goal_code") not in allowed or len(evidence) < 25
                or evidence.lower() not in t_norm or len(str(idea.get("idea", ""))) < 25
                or len(str(idea.get("action", ""))) < 20):
            continue
        clean.append({k: str(idea.get(k, "")).strip() for k in (
            "goal_code", "idea", "why_for_owner", "action", "metric",
            "fact", "evidence", "confidence")})
    return clean


def valid_id(x):
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(x))[:55]


def transcript_path(task_id, vid, title, text, provider, day):
    name = f"{day}_{valid_id(task_id)}_{vid}.md"
    path = SOURCE_DIR / name
    if not path.exists():
        path.write_text(
            f"# {title}\n\nSource: https://www.youtube.com/watch?v={vid}\n"
            f"Transcript method: {provider}\n\n## Transcript\n\n{text}\n",
            encoding="utf-8")
    return f"https://github.com/{REPO}/blob/mainggg/transcripts/{name}"


def result_rows(task, video, ideas, t_url, day):
    task_id = str(cell(task, 0))
    vid = video["id"]
    url = f"https://www.youtube.com/watch?v={vid}"
    kind = "short" if video.get("duration") and int(video["duration"]) <= 180 else "video"
    video_row = [vid, task_id, day, kind, video["title"], video["channel"],
                 url, video.get("published", ""), video.get("duration") or "",
                 t_url, "Обработано AI", "NEW SIGNAL" if ideas else "IGNORE",
                 ideas[0]["fact"] if ideas else "",
                 ideas[0]["action"] if ideas else "", cell(task, 15)]
    base, options = [], []
    for idea in ideas:
        key = hashlib.sha256((task_id + vid + idea["idea"]).encode()).hexdigest()[:12]
        kid = "AI_" + valid_id(task_id) + "_" + vid + "_" + key
        confidence = idea["confidence"] if idea["confidence"] in ("TEST", "NEW SIGNAL") else "TEST"
        base.append([kid, cell(task, 2), idea["idea"], day, vid, confidence,
                     idea["fact"] + " | Цитата: " + idea["evidence"],
                     idea["action"], idea["goal_code"], idea["metric"],
                     "Основано на транскрипте; проверить на своих данных", "На тесте",
                     "", cell(task, 15), idea["why_for_owner"]])
        options.append([day, idea["goal_code"], idea["idea"], idea["why_for_owner"],
                        idea["action"], idea["metric"], kid, url,
                        "Гипотеза / источник: транскрипт", "К проверке"])
    return video_row, base, options


def run(args):
    sh = Sheets()
    raw = sh.get("Задания", "A1:Y100")
    goals = sh.get("Мои цели", "A1:G100")
    rules = sh.get("Правила анализа", "A1:C100")
    videos = sh.get("Видео", "A1:O1000")
    knowledge = sh.get("База знаний", "A1:O1000")
    ideas = sh.get("Идеи для владельца", "A1:J200")
    history = sh.get("История", "A1:K1000")
    print(f"GOOGLE_SHEETS_READ=OK tasks={len(raw)-1} goals={len(goals)-1}")
    if not raw or not goals or not rules or not ideas:
        fail("Missing expected sheet data")
    existing = {(str(cell(r, 0)), str(cell(r, 1))) for r in videos[1:]
                if str(cell(r, 10)) in ("Обработано AI", "Проанализировано")}
    idea_urls = {str(cell(r, 7)) for r in ideas[1:] if cell(r, 7)}
    now = now_msk()
    tasks = [(i, r) for i, r in enumerate(raw[1:], start=2)
             if due(r, now, force=bool(args.force_task) and str(cell(r, 0)) == args.force_task)]
    if args.force_task:
        tasks = [(i, r) for i, r in enumerate(raw[1:], start=2)
                 if str(cell(r, 0)) == args.force_task and active(r)]
    tasks.sort(key=lambda p: (str(cell(p[1], 12)) != "Высокий", str(cell(p[1], 8))))
    tasks = tasks[:args.max_tasks]
    print(f"PLANNER_DUE={len(tasks)} date={now.date().isoformat()}")
    if args.dry_run:
        for rownum, r in tasks:
            print(f"DUE {cell(r,0)} row={rownum}")
        return
    for rownum, task in tasks:
        task_id = str(cell(task, 0))
        day = now_msk().strftime("%Y-%m-%d %H:%M")
        start = time.monotonic()
        collected, nideas, errors, newvid, newkb, newideas = 0, 0, [], [], [], []
        try:
            base = str(cell(task, 3)).strip()
            extra = [s.strip() for s in str(cell(task, 4)).split("|") if s.strip()]
            query = extra[(now.timetuple().tm_yday // 3) % len(extra)] if extra and now.day % 2 else base
            found = search_youtube(query, args.search_limit)
            maxvid = min(args.videos_per_task, max(1, int(cell(task, 9) or 5)))
            selected = [v for v in found if (v["id"], task_id) not in existing][:maxvid]
            print(f"SEARCH task={task_id} found={len(found)} selected={len(selected)}")
            for v in selected:
                try:
                    transcript, provider = transcript_for(v["id"])
                    t_url = transcript_path(task_id, v["id"], v["title"], transcript,
                                            provider, now.strftime("%Y-%m-%d"))
                    extracted = model_json(task, v, transcript, goals, rules)
                    if f"https://www.youtube.com/watch?v={v['id']}" in idea_urls:
                        extracted = []
                    vrow, kbrows, idearows = result_rows(task, v, extracted, t_url,
                                                          now.strftime("%Y-%m-%d"))
                    newvid.append(vrow)
                    newkb.extend(kbrows)
                    newideas.extend(idearows)
                    collected += 1
                    nideas += len(idearows)
                except Exception as ex:
                    errors.append(f"{v['id']}:{str(ex)[:95]}")
                    print(f"RETRY_PENDING task={task_id} vid={v['id']} error={type(ex).__name__}")
            # Atomic cross-tab value update: no fabricated rows on failure.
            elapsed = int(time.monotonic() - start)
            status = "Активно" if not errors or collected else "Ошибка"
            digest = f"транскриптов {collected}; новых идей {nideas}; ошибок {len(errors)}; {elapsed}с"
            runid = f"AUTOV3_{valid_id(task_id)}_{now.strftime('%Y%m%d_%H%M%S')}"
            hr = [runid, task_id, day, now_msk().strftime("%Y-%m-%d %H:%M"),
                  query, collected, 0, "; ".join(errors)[:700], status, digest,
                  "GitHub Actions ai-video-monitor-autonomous.yml"]
            blocks = []
            def addmany(name, matrix, previous):
                for offset, rr in enumerate(matrix):
                    end_col = chr(ord("A") + len(rr) - 1)
                    blocks.append((name, f"A{previous+offset+1}:{end_col}{previous+offset+1}", rr))
            addmany("История", [hr], len(history))
            addmany("Видео", newvid, len(videos))
            addmany("База знаний", newkb, len(knowledge))
            addmany("Идеи для владельца", newideas, len(ideas))
            values = {
                "Q": day,
                "R": next_run(task, now),
                "S": status,
                "T": int(collected),
                "U": int(sum(1 for r in newvid if r[3] == "short")),
                "V": "; ".join(errors)[:600] if errors else "",
                "Y": runid,
            }
            for col, value in values.items():
                blocks.append(("Задания", f"{col}{rownum}", value))
            # Append only rows with validated source and grounded AI. Retry errors next schedule.
            sh.batch(blocks)
            history.append(hr)
            videos.extend(newvid)
            knowledge.extend(newkb)
            ideas.extend(newideas)
            existing.update((r[0], r[1]) for r in newvid)
            idea_urls.update(r[7] for r in newideas)
            print(f"COMMITTED task={task_id} transcripts={collected} ideas={nideas} errors={len(errors)}")
        except Exception as ex:
            print(f"TASK_FAILURE task={task_id} category={type(ex).__name__} reason={str(ex)[:150]}")
            if args.fail_fast:
                raise


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force-task", default="")
    parser.add_argument("--max-tasks", type=int, default=4)
    parser.add_argument("--videos-per-task", type=int, default=2)
    parser.add_argument("--search-limit", type=int, default=14)
    parser.add_argument("--fail-fast", action="store_true")
    run(parser.parse_args())


if __name__ == "__main__":
    main()
