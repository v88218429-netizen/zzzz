#!/usr/bin/env python3
import json
import pathlib
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parent
ANALYSIS = ROOT / "analysis"
FRAGMENTS = ANALYSIS / "HESSDALEN_GACP_20260929_fragments.json"
OUT_MD = ANALYSIS / "HESSDALEN_GACP_20260929_PRIMARY_M_REPORT.md"
OUT_JSON = ANALYSIS / "HESSDALEN_GACP_20260929_PRIMARY_M_DISTILLED.json"

PRIMARY_SIGNALS = (
    "project hessdalen",
    "hessdalen",
    "massimo",
    "teodorani",
    "blue box",
    "openads",
    "open ads",
)

CATEGORY_RULES = [
    ("01_camera_artifact_control", ("camera", "sensor", "raw", "jpeg", "compression", "reflection", "triangulation", "scientific camera", "motion detection")),
    ("02_spectrometry_emission", ("spectrum", "spectrometer", "spectrometry", "laser", "emission", "white light", "broad spectrum")),
    ("03_neutron_radiation", ("neutron", "radiation", "detector", "detection")),
    ("04_object_behavior", ("luminous", "light", "orb", "motion", "moving", "shape", "spherical", "oval", "toroidal", "duration", "hover", "stable")),
    ("05_plasma_gacp_hypothesis", ("plasma", "plasmoid", "ball lightning", "coherent", "geophysical", "anchored")),
    ("06_open_data_infrastructure", ("openads", "open ads", "data", "telemetry", "mqtt", "cloud", "sensor")),
]


def load_fragments():
    data = json.loads(FRAGMENTS.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        for key in ("fragments", "items", "rows"):
            if isinstance(data.get(key), list):
                return data[key]
        return [data]
    return data


def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()


def is_primaryish(row):
    sc = str(row.get("source_class", "")).lower()
    if sc == "primary_or_hessdalen_specific":
        return True
    blob = " ".join(str(row.get(k, "")) for k in ("title", "channel", "fragment", "terms", "groups")).lower()
    return any(x in blob for x in PRIMARY_SIGNALS)


def category(row):
    blob = " ".join(str(row.get(k, "")) for k in ("terms", "groups", "evidence_type", "object_type", "fragment", "title")).lower()
    hits = []
    for name, keys in CATEGORY_RULES:
        score = sum(1 for k in keys if k in blob)
        if score:
            hits.append((score, name))
    if not hits:
        return "99_other"
    hits.sort(reverse=True)
    return hits[0][1]


def clean_fragment(text):
    text = norm(text)
    # Remove repeated adjacent word runs from bad YouTube transcript duplication.
    words = text.split()
    out = []
    i = 0
    while i < len(words):
        repeated = False
        for size in range(min(18, (len(words)-i)//2), 3, -1):
            if words[i:i+size] == words[i+size:i+2*size]:
                out.extend(words[i:i+size])
                i += size * 2
                repeated = True
                break
        if not repeated:
            out.append(words[i])
            i += 1
    text = " ".join(out)
    return text[:1400]


def usefulness_order(v):
    return {"high": 3, "medium": 2, "low": 1}.get(str(v).lower(), 0)


def main():
    rows = load_fragments()
    # Keep only useful + primary/Hessdalen-specific. Medium is included because instrumentation may score medium but is critical.
    filtered = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        if not is_primaryish(r):
            continue
        if usefulness_order(r.get("useful_for_M")) < 2:
            continue
        r = dict(r)
        r["category"] = category(r)
        r["fragment_clean"] = clean_fragment(r.get("fragment"))
        filtered.append(r)

    # Deduplicate noisy duplicate transcript fragments.
    seen = set()
    deduped = []
    for r in sorted(filtered, key=lambda x: (category(x), -int(x.get("score") or 0), str(x.get("title")), str(x.get("timestamp")))):
        key = re.sub(r"[^a-z0-9а-я]+", " ", r.get("fragment_clean", "").lower())[:240]
        source_key = (r.get("url"), r.get("timestamp"), key)
        if source_key in seen:
            continue
        seen.add(source_key)
        deduped.append(r)

    by_cat = defaultdict(list)
    for r in deduped:
        by_cat[r["category"]].append(r)

    def one_line(row):
        return {
            "url": row.get("url"),
            "channel": row.get("channel"),
            "title": row.get("title"),
            "timestamp": row.get("timestamp"),
            "transcript": row.get("transcript"),
            "terms": row.get("terms"),
            "groups": row.get("groups"),
            "source_class": row.get("source_class"),
            "evidence_type": row.get("evidence_type"),
            "object_type": row.get("object_type"),
            "useful_for_M": row.get("useful_for_M"),
            "score": row.get("score"),
            "category": row.get("category"),
            "fragment": row.get("fragment_clean"),
            "next_action": row.get("next_action"),
        }

    distilled = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_fragments": str(FRAGMENTS.relative_to(ROOT)),
        "input_fragment_count": len(rows),
        "filtered_primary_medium_high": len(filtered),
        "deduped_count": len(deduped),
        "counts_by_category": {k: len(v) for k, v in sorted(by_cat.items())},
        "counts_by_video": Counter(r.get("title") for r in deduped),
        "top_fragments_by_category": {
            cat: [one_line(r) for r in items[:12]] for cat, items in sorted(by_cat.items())
        },
    }
    OUT_JSON.write_text(json.dumps(distilled, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    lines = []
    lines.append("# Hessdalen/GACP → primary evidence distillation for Object M")
    lines.append("")
    lines.append(f"- Generated: {distilled['generated_at']}")
    lines.append(f"- Source fragments: `{distilled['source_fragments']}`")
    lines.append(f"- Input keyword fragments: {len(rows)}")
    lines.append(f"- Primary/Hessdalen-specific medium+high fragments: {len(filtered)}")
    lines.append(f"- Deduped fragments used below: {len(deduped)}")
    lines.append("")
    lines.append("## What matters for M")
    lines.append("")
    lines.append("The usable signal is not 'plasmoids are proven'. The usable signal is an instrument protocol: raw/non-compressed imaging, separated cameras to reject reflections, spectrum/spectrometer checks, optional neutron/radiation checks, mobile observation stations, and open telemetry/data handling.")
    lines.append("")
    lines.append("## Counts by category")
    lines.append("")
    for cat, cnt in sorted(distilled["counts_by_category"].items()):
        lines.append(f"- {cat}: {cnt}")
    lines.append("")
    lines.append("## Category distillation")
    for cat, items in sorted(by_cat.items()):
        lines.append("")
        lines.append(f"### {cat}")
        lines.append("")
        for idx, r in enumerate(items[:8], 1):
            lines.append(f"#### {idx}. {r.get('title')} — {r.get('channel')}")
            lines.append(f"- URL: {r.get('url')}")
            lines.append(f"- Timestamp: {r.get('timestamp')}")
            lines.append(f"- Transcript: `{r.get('transcript')}`")
            lines.append(f"- Terms: {r.get('terms')}")
            lines.append(f"- Evidence type: {r.get('evidence_type')}")
            lines.append(f"- Useful for M: {r.get('useful_for_M')} / score {r.get('score')}")
            lines.append(f"- Fragment: {r.get('fragment_clean')}")
            lines.append("")
    lines.append("## Operational protocol for Object M")
    lines.append("")
    lines.append("1. Do not start with 'what is it?'; start with 'can we remove artifact classes?' Reflection, JPEG/compression, insects, dust, spider web, lens flare, and single-camera ambiguity must be killed first.")
    lines.append("2. Use at least two synchronized cameras separated by meters, viewing the same region. A reflection/lens artifact should not triangulate as a stable external object.")
    lines.append("3. Prefer raw/non-compressed capture or the least compressed mode available. JPEG edge artifacts are explicitly a problem in Hessdalen-style work.")
    lines.append("4. Add environment notes: humidity, wind, temperature, dust/smoke/aerosol, moon, headlights, streetlights, power lines, insects, and car-independent observations.")
    lines.append("5. If repeated, add spectrum/spectrometer or diffraction-grating video. Broad/line spectrum is a stronger discriminator than color in ordinary video.")
    lines.append("6. Treat neutron/radiation detection as a later branch, not first equipment. It is expensive/noisy and only justified if optical recurrence is established.")
    lines.append("7. Keep hypothesis ladder: artifact → atmospheric/aerosol/vortex → electrical/plasma/ball-lightning → GACP-like model. Do not jump to the last rung.")
    lines.append("")
    OUT_MD.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"Wrote {OUT_MD}, {OUT_JSON}")
    print(f"filtered={len(filtered)} deduped={len(deduped)} categories={dict(distilled['counts_by_category'])}")

if __name__ == "__main__":
    main()
