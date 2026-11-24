"""Turn timed marks from a recorder into a clip spec that tools/process.py renders.

Marks are dicts {"name": str, "t": unix seconds, optional "x"/"y": 0..1 of the screen}.
Names: "start" (optional), "tap*" (a tap; the first one starts the clip LEAD s earlier
unless "start" exists), "cut_from"/"cut_to" (dead time to drop), "end".
"""
import json

LEAD = 1.0


def build(clip, raw, marks, t0, status="ok", note="", overlay_taps=False):
    def rel(t):
        return round(t - t0, 3)

    by = {}
    for m in marks:
        by.setdefault(m["name"], []).append(m)
    taps = [m for m in marks if m["name"].startswith("tap")]
    spec = {"clip": clip, "raw": raw, "status": status, "note": note,
            "marks": [{**m, "rel": rel(m["t"])} for m in marks]}
    if status != "ok" or "end" not in by:
        spec["segments"] = []
        return spec
    if "start" in by:
        start = rel(by["start"][0]["t"])
    elif taps:
        start = rel(taps[0]["t"]) - LEAD
    else:
        start = 0.0
    start = max(0.0, start)
    end = rel(by["end"][-1]["t"])
    segments, cur = [], start
    for a, b in zip(by.get("cut_from", []), by.get("cut_to", [])):
        if rel(b["t"]) - rel(a["t"]) > 0.5:
            segments.append([cur, rel(a["t"])])
            cur = rel(b["t"])
    segments.append([cur, end])
    spec["segments"] = [[round(a, 3), round(b, 3)] for a, b in segments if b > a]
    if overlay_taps:
        spec["taps"] = [{"t": rel(m["t"]), "x": m["x"], "y": m["y"]} for m in taps if "x" in m]
    return spec


def write(path, spec):
    with open(path, "w") as f:
        json.dump(spec, f, indent=2, ensure_ascii=False)
