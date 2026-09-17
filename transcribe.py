#!/usr/bin/env python3
import json, os, re, subprocess, sys, pathlib, html
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent
REQ = ROOT / "requests"
OUT = ROOT / "transcripts"
OUT.mkdir(exist_ok=True)


def run(cmd, check=True):
    p = subprocess.run(cmd, text=True, capture_output=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}")
    return p


def clean_vtt(text):
    lines = []
    seen = set()
    for raw in text.splitlines():
        s = raw.strip()
        if not s or s.startswith("WEBVTT") or s.startswith("Kind:") or s.startswith("Language:") or "-->" in s or re.fullmatch(r"\d+", s):
            continue
        s = re.sub(r"<[^>]+>", "", s)
        s = html.unescape(s)
        s = re.sub(r"\s+", " ", s).strip()
        if s and s not in seen:
            lines.append(s); seen.add(s)
    return "\n".join(lines)


def transcribe(url, slug):
    work = ROOT / ".work" / slug
    work.mkdir(parents=True, exist_ok=True)
    meta = run(["yt-dlp", "--dump-single-json", "--no-warnings", url]).stdout
    info = json.loads(meta)
    title = info.get("title") or slug
    uploader = info.get("uploader") or info.get("channel") or ""
    duration = info.get("duration")

    # First try embedded/automatic Russian subtitles.
    sub_tpl = str(work / "subs.%(ext)s")
    p = run([
        "yt-dlp", "--skip-download", "--write-subs", "--write-auto-subs",
        "--sub-langs", "ru.*,ru", "--sub-format", "vtt", "-o", sub_tpl, url
    ], check=False)
    vtts = sorted(work.glob("subs*.vtt"))
    method = "subtitles"
    transcript = ""
    if vtts:
        transcript = clean_vtt(vtts[0].read_text(encoding="utf-8", errors="ignore"))

    if not transcript.strip():
        method = "whisper"
        audio = work / "audio.mp3"
        run([
            "yt-dlp", "-x", "--audio-format", "mp3", "--audio-quality", "5",
            "-o", str(work / "audio.%(ext)s"), url
        ])
        candidates = list(work.glob("audio.*"))
        if not candidates:
            raise RuntimeError("audio download produced no file")
        audio = candidates[0]
        # whisper.cpp/faster-whisper CLI is installed by workflow.
        result = run([
            "whisper", str(audio), "--model", "small", "--language", "Russian",
            "--task", "transcribe", "--output_format", "txt", "--output_dir", str(work)
        ])
        txts = sorted(work.glob("*.txt"))
        if not txts:
            raise RuntimeError("whisper produced no txt")
        transcript = txts[-1].read_text(encoding="utf-8", errors="ignore")

    md = []
    md.append(f"# {title}")
    md.append("")
    md.append(f"- Source: {url}")
    if uploader: md.append(f"- Author/channel: {uploader}")
    if duration: md.append(f"- Duration: {int(duration)//60}:{int(duration)%60:02d}")
    md.append(f"- Transcript method: {method}")
    md.append(f"- Generated: {datetime.now(timezone.utc).isoformat()}")
    md.append("")
    md.append("## Transcript")
    md.append("")
    md.append(transcript.strip())
    out = OUT / f"{slug}.md"
    out.write_text("\n".join(md) + "\n", encoding="utf-8")
    return out


def main():
    files = sorted(REQ.glob("*.json"))
    if not files:
        print("No request files")
        return
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        url = data["url"]
        slug = f.stem
        print(f"Transcribing {url}")
        transcribe(url, slug)

if __name__ == "__main__":
    main()
