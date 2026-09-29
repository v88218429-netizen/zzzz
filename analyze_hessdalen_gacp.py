#!/usr/bin/env python3
"""Compact keyword/evidence analyzer for the HESSDALEN_GACP video task.

Reads the generated monitor_tasks/results JSON, scans the full transcript files,
and writes small human-readable reports to analysis/ so ChatGPT/connectors can
consume the important fragments without loading huge transcripts.
"""
import csv
import json
import pathlib
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent
RESULT = ROOT / "monitor_tasks" / "results" / "HESSDALEN_GACP_20260929_1608.json"
ANALYSIS = ROOT / "analysis"
ANALYSIS.mkdir(exist_ok=True)
OUT_MD = ANALYSIS / "HESSDALEN_GACP_20260929_keyword_report.md"
OUT_CSV = ANALYSIS / "HESSDALEN_GACP_20260929_keyword_table.csv"
OUT_JSON = ANALYSIS / "HESSDALEN_GACP_20260929_fragments.json"

KEYWORD_GROUPS = {
    "gacp_core": ["geophysically", "anchored", "coherent", "plasmoid", "plasma", "ball lightning"],
    "instrument_neutron": ["neutron", "neutrons", "radiation", "detector", "detection"],
    "instrument_spectrometry": ["spectrometer", "spectrometry", "spectrum", "spectral", "tunable laser", "laser"],
    "instrument_camera_sensor": ["scientific camera", "raw sensor", "sensor data", "raw image", "compression", "jpeg", "artifact", "artifacts", "reflection", "reflections", "triangulation"],
    "project_terms": ["hessdalen", "blue box", "openads", "open ads", "uap summit", "massimo", "teodorani"],
    "object_behavior": ["light phenomena", "phenomena", "orb", "orbs", "luminous", "hover", "moving", "motion", "anomalous"],
}

ALL_TERMS = sorted({term for terms in KEYWORD_GROUPS.values() for term in terms}, key=len, reverse=True)
TS_RE = re.compile(r"^\[(\d{1,2}:\d{2}(?::\d{2})?)\]\s*(.*)$")


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def line_records(text: str):
    rows = []
    for raw in text.splitlines():
        m = TS_RE.match(raw.strip())
        if m:
            rows.append({"ts": m.group(1), "text": norm(m.group(2)), "raw": raw.strip()})
    return rows


def hit_terms(text: str):
    low = text.lower()
    return [t for t in ALL_TERMS if t in low]


def hit_groups(terms):
    out = []
    st = set(terms)
    for group, group_terms in KEYWORD_GROUPS.items():
        if any(t in st for t in group_terms):
            out.append(group)
    return out


def classify(groups, terms, title, channel):
    t = " ".join([title or "", channel or "", " ".join(terms)]).lower()
    if "project hessdalen" in (channel or "").lower() or "hessdalen" in t:
        source_class = "primary_or_hessdalen_specific"
    elif "massimo" in t or "teodorani" in t:
        source_class = "researcher_interview"
    elif "thunderbolts" in t or "alchemical" in t or "ancient" in t:
        source_class = "speculative_plasmoid_media"
    else:
        source_class = "secondary_or_popular"

    if "instrument_camera_sensor" in groups or "instrument_neutron" in groups or "instrument_spectrometry" in groups:
        evidence_type = "instrumentation_or_method"
    elif "gacp_core" in groups:
        evidence_type = "conceptual_plasma_plasmoid_claim"
    else:
        evidence_type = "context_or_popular_claim"

    if any(x in t for x in ["artifact", "reflection", "jpeg", "compression", "raw sensor", "raw image"]):
        object_type = "artifact_control_or_sensor_issue"
    elif any(x in t for x in ["neutron", "spectrometer", "spectral", "camera", "sensor"]):
        object_type = "measurement_setup"
    elif any(x in t for x in ["plasmoid", "plasma", "ball lightning", "light phenomena", "orb"]):
        object_type = "luminous_plasma_or_orb_candidate"
    else:
        object_type = "general_context"

    score = 0
    score += 3 if source_class == "primary_or_hessdalen_specific" else 0
    score += 2 if "instrument_camera_sensor" in groups else 0
    score += 2 if "instrument_neutron" in groups else 0
    score += 2 if "instrument_spectrometry" in groups else 0
    score += 2 if "gacp_core" in groups else 0
    score += 1 if "object_behavior" in groups else 0
    if source_class == "speculative_plasmoid_media":
        score -= 2
    useful = "high" if score >= 5 else "medium" if score >= 3 else "low"
    return source_class, evidence_type, object_type, useful, score


def context_fragment(rows, idx, radius=2):
    start = max(0, idx - radius)
    end = min(len(rows), idx + radius + 1)
    return " ".join(f"[{r['ts']}] {r['text']}" for r in rows[start:end])


def main():
    if not RESULT.exists():
        raise SystemExit(f"Missing result file: {RESULT}")
    data = json.loads(RESULT.read_text(encoding="utf-8"))
    videos = data.get("videos") or []
    errors = data.get("errors") or []

    fragments = []
    per_video_counts = []
    for v in videos:
        rel = v.get("transcript")
        path = ROOT / rel if rel else None
        if not path or not path.exists():
            per_video_counts.append({**v, "matched_fragments": 0, "matched_terms": []})
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        rows = line_records(text)
        seen_fragments = set()
        video_terms = Counter()
        for i, row in enumerate(rows):
            terms = hit_terms(row["text"])
            if not terms:
                continue
            groups = hit_groups(terms)
            video_terms.update(terms)
            source_class, evidence_type, object_type, useful, score = classify(groups, terms, v.get("title", ""), v.get("channel", ""))
            frag = context_fragment(rows, i, 2)
            dedupe = (v.get("video_id"), row["ts"], tuple(terms))
            if dedupe in seen_fragments:
                continue
            seen_fragments.add(dedupe)
            fragments.append({
                "video_id": v.get("video_id"),
                "url": v.get("url"),
                "channel": v.get("channel"),
                "title": v.get("title"),
                "duration": v.get("duration"),
                "query": v.get("query"),
                "transcript": rel,
                "timestamp": row["ts"],
                "terms": terms,
                "groups": groups,
                "source_class": source_class,
                "evidence_type": evidence_type,
                "object_type": object_type,
                "useful_for_M": useful,
                "score": score,
                "fragment": frag,
                "next_action": "read_full_context" if useful == "high" else "defer_or_sample",
            })
        per_video_counts.append({
            **v,
            "matched_fragments": sum(1 for f in fragments if f["video_id"] == v.get("video_id")),
            "matched_terms": [k for k, _ in video_terms.most_common(20)],
        })

    fragments.sort(key=lambda f: (-f["score"], f["source_class"], f["title"] or "", f["timestamp"]))
    top = fragments[:160]

    with OUT_JSON.open("w", encoding="utf-8") as f:
        json.dump({
            "task_id": data.get("task_id"),
            "request": data.get("request"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "videos_total": len(videos),
            "errors_total": len(errors),
            "fragments_total": len(fragments),
            "videos": per_video_counts,
            "errors": errors,
            "fragments": top,
        }, f, ensure_ascii=False, indent=2)
        f.write("\n")

    fieldnames = ["url", "channel", "title", "timestamp", "transcript", "terms", "groups", "source_class", "evidence_type", "object_type", "useful_for_M", "score", "fragment", "next_action"]
    with OUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in top:
            w.writerow({k: ("; ".join(row[k]) if isinstance(row.get(k), list) else row.get(k, "")) for k in fieldnames})

    useful_counts = Counter(f["useful_for_M"] for f in fragments)
    group_counts = Counter(g for f in fragments for g in f["groups"])
    source_counts = Counter(f["source_class"] for f in fragments)
    high_videos = [v for v in per_video_counts if v.get("matched_fragments", 0) > 0]
    high_videos.sort(key=lambda v: (-(v.get("matched_fragments") or 0), v.get("title") or ""))

    md = []
    md.append("# HESSDALEN_GACP keyword/evidence report")
    md.append("")
    md.append(f"- Generated: {datetime.now(timezone.utc).isoformat()}")
    md.append(f"- Request: `{data.get('request')}`")
    md.append(f"- Videos in result: {len(videos)}")
    md.append(f"- Transcript errors in result: {len(errors)}")
    md.append(f"- Keyword fragments found: {len(fragments)}")
    md.append(f"- Compact JSON: `{OUT_JSON.relative_to(ROOT)}`")
    md.append(f"- Compact CSV: `{OUT_CSV.relative_to(ROOT)}`")
    md.append("")
    md.append("## Counts")
    md.append("")
    md.append("### Useful for M")
    for k, c in useful_counts.most_common():
        md.append(f"- {k}: {c}")
    md.append("")
    md.append("### Keyword groups")
    for k, c in group_counts.most_common():
        md.append(f"- {k}: {c}")
    md.append("")
    md.append("### Source classes")
    for k, c in source_counts.most_common():
        md.append(f"- {k}: {c}")
    md.append("")
    md.append("## Videos with hits")
    md.append("")
    for v in high_videos[:40]:
        md.append(f"- **{v.get('title')}** — {v.get('channel')} — hits: {v.get('matched_fragments')} — `{v.get('transcript')}`")
    md.append("")
    md.append("## Top fragments")
    md.append("")
    for i, f in enumerate(top[:80], 1):
        md.append(f"### {i}. {f['title']}")
        md.append(f"- Channel: {f['channel']}")
        md.append(f"- URL: {f['url']}")
        md.append(f"- Transcript: `{f['transcript']}`")
        md.append(f"- Timestamp: {f['timestamp']}")
        md.append(f"- Terms: {', '.join(f['terms'])}")
        md.append(f"- Groups: {', '.join(f['groups'])}")
        md.append(f"- Source class: {f['source_class']}")
        md.append(f"- Evidence type: {f['evidence_type']}")
        md.append(f"- Object type: {f['object_type']}")
        md.append(f"- Useful for M: {f['useful_for_M']} / score {f['score']}")
        md.append(f"- Fragment: {f['fragment']}")
        md.append("")
    if errors:
        md.append("## Transcript errors")
        md.append("")
        for e in errors[:50]:
            md.append(f"- {e.get('title') or e.get('video_id')}: {e.get('url')} — {e.get('error')}")
        md.append("")
    OUT_MD.write_text("\n".join(md).rstrip() + "\n", encoding="utf-8")
    print(f"Wrote {OUT_MD}, {OUT_CSV}, {OUT_JSON}")
    print(f"videos={len(videos)} errors={len(errors)} fragments={len(fragments)}")


if __name__ == "__main__":
    main()
