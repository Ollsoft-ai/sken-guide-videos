#!/usr/bin/env python3
"""Render looping guide clips from raw recordings.

usage: process.py RAW_DIR OUT_DIR
RAW_DIR holds <clip>.json specs (see clipspec.py) next to the raw recordings.
Writes OUT_DIR/<clip>.mp4 (H.264) and <clip>.webm (VP9), portrait, no audio,
width 540, <= 2 MB, plus OUT_DIR/summary.md. Exits 1 if no clip was rendered.
"""
import glob
import json
import re
import os
import subprocess
import sys

WIDTH = 540
MAX_BYTES = 2_000_000
MAX_SECONDS = 10.0
FPS = 30
TAP_DELAY = 0.1   # used when a tap cannot be snapped to the screen's reaction
PAD = 4.0         # recorders emit no frames for a still screen: clone the last frame
MIN_CUT = 0.5     # never cut less than this out of a still period
ARROW_BEFORE = 0.75  # red arrow appears this long before a tap ...
ARROW_AFTER = 0.15   # ... and stays this long after it
CIRCLE_SHOW = 0.55   # red circle at the tap point
RED = (235, 20, 20, 255)
WHITE = (255, 255, 255, 255)
# Still-screen budgets (seconds), loosest first; the first that fits MAX_SECONDS wins.
# lead: before the first tap; tap: (after settling, before the tap) of a screen the user
# acts on (the tail must hold the arrow); load: (head, tail) of a still screen nobody acts
# on (waits, loading); end: final hold. Motion is never cut and never slowed.
BUDGETS = [
    {"lead": 1.0, "tap": (0.5, 0.8), "load": (0.15, 0.15), "end": 2.0},
    {"lead": 0.9, "tap": (0.35, 0.8), "load": (0.1, 0.1), "end": 1.8},
    {"lead": 0.8, "tap": (0.25, 0.8), "load": (0.05, 0.05), "end": 1.6},
    {"lead": 0.8, "tap": (0.1, 0.8), "load": (0.0, 0.05), "end": 1.4},
]


def run(cmd):
    print("+", " ".join(cmd)[:300], flush=True)
    subprocess.run(cmd, check=True)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height:format=duration",
                          "-of", "json", path], check=True, capture_output=True, text=True).stdout
    j = json.loads(out)
    s = j["streams"][0]
    dur = j.get("format", {}).get("duration")
    if dur is None:  # streamed webm: last packet time
        pts = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
                              "-of", "csv=p=0", path], capture_output=True, text=True).stdout.split()
        dur = max([float(v) for v in pts if re.match(r"^[0-9.]+$", v)] or [0])
    return s["width"], s["height"], float(dur)


def markers(tmp, i, tap, ow, oh):
    """Full-frame transparent PNGs for one tap: a big red ring at the tap point and a red
    arrow pointing at it from the screen-centre side (white outlines for contrast)."""
    import math
    from PIL import Image, ImageDraw
    x, y = tap["x"] * ow, tap["y"] * oh
    r = ow * 0.085
    ring = Image.new("RGBA", (ow, oh), (0, 0, 0, 0))
    d = ImageDraw.Draw(ring)
    d.ellipse([x - r - 3, y - r - 3, x + r + 3, y + r + 3], fill=(235, 20, 20, 70), outline=WHITE, width=3)
    d.ellipse([x - r, y - r, x + r, y + r], outline=RED, width=max(6, round(ow * 0.016)))
    d.ellipse([x - r * 0.16, y - r * 0.16, x + r * 0.16, y + r * 0.16], fill=RED)
    vx, vy = ow / 2 - x, oh / 2 - y
    if math.hypot(vx, vy) < ow * 0.12:
        vx, vy = 1.0, 0.7
    n = math.hypot(vx, vy)
    ux, uy = vx / n, vy / n                     # from the target towards the screen centre
    length, head, hw, sw = ow * 0.2, ow * 0.065, ow * 0.045, ow * 0.016
    tipx, tipy = x + ux * (r + 6), y + uy * (r + 6)
    px, py = -uy, ux                            # perpendicular

    def pt(along, side):
        return (tipx + ux * along + px * side, tipy + uy * along + py * side)
    poly = [pt(0, 0), pt(head, hw), pt(head, sw), pt(length, sw), pt(length, -sw), pt(head, -sw), pt(head, -hw)]
    arrow = Image.new("RGBA", (ow, oh), (0, 0, 0, 0))
    d = ImageDraw.Draw(arrow)
    d.polygon(poly, fill=WHITE, outline=WHITE, width=7)
    d.polygon(poly, fill=RED)
    rp, ap = os.path.join(tmp, f"ring{i}.png"), os.path.join(tmp, f"arrow{i}.png")
    ring.save(rp)
    arrow.save(ap)
    return rp, ap


def out_time(t, segs):
    """Raw-recording time -> time in the cut clip."""
    acc = 0.0
    for a, b in segs:
        if t < a:
            return acc
        if t <= b:
            return acc + t - a
        acc += b - a
    return acc


def freezes(path):
    """Still periods [(start, end)] of the recording (the last one may run to the end)."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", path, "-vf",
                        f"setpts=PTS-STARTPTS,fps=10,scale=270:-2,tpad=stop_mode=clone:stop_duration={PAD},"
                        "freezedetect=n=0.003:d=0.5", "-an", "-f", "null", "-"],
                       capture_output=True, text=True)
    out, cur = [], None
    for k, v in re.findall(r"freeze_(start|end): ([0-9.]+)", r.stderr):
        if k == "start":
            cur = float(v)
        elif cur is not None:
            out.append((cur, float(v)))
            cur = None
    if cur is not None:
        out.append((cur, 1e9))
    return out


def snap_taps(taps, frz):
    """Move each tap to just before the screen reacts (the end of the still period the tap
    ends): recorder clock offsets and XCUITest tap latency are both up to ~3 s."""
    ends = sorted(e for _, e in frz if e < 1e9)
    out = []
    for t in taps:
        cand = [e for e in ends if t["t"] - 0.6 <= e <= t["t"] + 3.5]
        out.append({**t, "t": round(cand[0] - 0.15, 3) if cand else t["t"] + TAP_DELAY, "snapped": bool(cand)})
    return out


def compress(segments, frz, taps, b):
    """Cut the middle out of still periods: a screen the user acts on keeps b["tap"],
    a wait/loading screen keeps b["load"], the first and last keep b["lead"]/b["end"]."""
    tap_times = [t["t"] for t in taps]
    out = []
    for a, z in segments:
        cuts = []
        for fs, fe in frz:
            s, e = max(fs, a), min(fe, z)
            if e - s <= 0:
                continue
            if e >= z - 0.05:
                if e - s > b["end"]:
                    cuts.append((s + b["end"], z))
                continue
            head, tail = b["tap"] if any(e - 0.6 <= t <= e + 0.4 for t in tap_times) else b["load"]
            if s <= a + 0.05:
                head = b["lead"] - tail
            if e - s - head - tail >= MIN_CUT:  # tiny cuts inside an animation would stutter
                cuts.append((s + head, e - tail))
        cur = a
        for cs, ce in sorted(cuts):
            if cs - cur > 0.05:
                out.append([round(cur, 3), round(cs, 3)])
            cur = max(cur, ce)
        if z - cur > 0.05:
            out.append([round(cur, 3), round(z, 3)])
    return out


def graph(segs, taps, ow, oh):
    """Cut (trim+concat, real time), then draw the arrow and ring of each tap on the cut timeline.
    Inputs: 0 = recording, then per tap 2i+1 = ring, 2i+2 = arrow (looped stills)."""
    chains = [f"[0:v]setpts=PTS-STARTPTS,fps={FPS},tpad=stop_mode=clone:stop_duration={PAD},"
              f"scale={ow}:{oh}:flags=lanczos,split={len(segs)}" + "".join(f"[s{i}]" for i in range(len(segs)))]
    for i, (a, b) in enumerate(segs):
        chains.append(f"[s{i}]trim=start={a}:end={b},setpts=PTS-STARTPTS[t{i}]")
    chains.append("".join(f"[t{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=0[c]")
    last = "c"
    for i, t in enumerate(taps):
        ot = out_time(t["t"], segs)
        chains.append(f"[{last}][{2 * i + 2}:v]overlay=0:0:shortest=1:"
                      f"enable='between(t,{max(0, ot - ARROW_BEFORE):.3f},{ot + ARROW_AFTER:.3f})'[a{i}]")
        chains.append(f"[a{i}][{2 * i + 1}:v]overlay=0:0:shortest=1:"
                      f"enable='between(t,{ot:.3f},{ot + CIRCLE_SHOW:.3f})'[r{i}]")
        last = f"r{i}"
    chains.append(f"[{last}]format=yuv420p[out]")
    return ";".join(chains)


def render(spec, raw_dir, out_dir, tmp):
    raw = os.path.join(raw_dir, spec["raw"])
    w, h, dur = probe(raw)
    frz = freezes(raw)
    taps = snap_taps(spec.get("taps", []), frz)
    base = [[a, min(z, dur + PAD - 0.1)] for a, z in spec["segments"]]
    for b in BUDGETS:
        segs = compress(base, frz, taps, b)
        total = sum(z - a for a, z in segs)
        if total <= MAX_SECONDS:
            break
    print(f"{spec['clip']}: {total:.1f}s budget {b} segments {segs} taps {taps}", flush=True)
    oh = round(h * WIDTH / w / 2) * 2
    inputs = ["-i", raw]
    for i, t in enumerate(taps):
        rp, ap = markers(tmp, f"{spec['clip']}-{i}", t, WIDTH, oh)
        inputs += ["-loop", "1", "-i", rp, "-loop", "1", "-i", ap]
    fg = graph(segs, taps, WIDTH, oh)
    name = spec["clip"]
    mp4 = os.path.join(out_dir, name + ".mp4")
    for crf in (23, 26, 29, 32, 35):
        run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", fg, "-map", "[out]", "-an",
             "-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-profile:v", "high",
             "-pix_fmt", "yuv420p", "-r", str(FPS), "-movflags", "+faststart", mp4])
        if os.path.getsize(mp4) <= MAX_BYTES:
            break
    webm = os.path.join(out_dir, name + ".webm")
    for crf in (36, 40, 44):
        run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", fg, "-map", "[out]", "-an",
             "-c:v", "libvpx-vp9", "-b:v", "0", "-crf", str(crf), "-row-mt", "1", "-deadline", "good",
             "-cpu-used", "4", "-r", str(FPS), webm])
        if os.path.getsize(webm) <= MAX_BYTES:
            break
    ow, oh, odur = probe(mp4)
    res = {"clip": name, "status": "ok", "mp4": os.path.basename(mp4), "webm": os.path.basename(webm),
           "size_mp4": os.path.getsize(mp4), "size_webm": os.path.getsize(webm),
           "duration": round(odur, 2), "width": ow, "height": oh, "note": spec.get("note", ""),
           "run": os.environ.get("GUIDE_RUN_URL", ""), "segments": segs, "taps": taps}
    json.dump(res, open(os.path.join(out_dir, name + ".json"), "w"), indent=2, ensure_ascii=False)
    return res


def main():
    raw_dir, out_dir = sys.argv[1], sys.argv[2]
    os.makedirs(out_dir, exist_ok=True)
    tmp = os.path.join(out_dir, ".tmp")
    os.makedirs(tmp, exist_ok=True)
    results = []
    for path in sorted(glob.glob(os.path.join(raw_dir, "*.json"))):
        spec = json.load(open(path))
        if spec.get("status") != "ok" or not spec.get("segments"):
            results.append({"clip": spec.get("clip"), "status": spec.get("status"), "note": spec.get("note", "")})
            continue
        try:
            results.append(render(spec, raw_dir, out_dir, tmp))
        except Exception as e:  # keep going, report it
            results.append({"clip": spec.get("clip"), "status": "render-failed", "note": str(e)})
    subprocess.run(["rm", "-rf", tmp])
    json.dump(results, open(os.path.join(out_dir, "results.json"), "w"), indent=2, ensure_ascii=False)
    lines = ["| clip | status | duration | mp4 | note |", "|-|-|-|-|-|"]
    for r in results:
        lines.append(f"| {r['clip']} | {r['status']} | {r.get('duration', '')} | {r.get('size_mp4', '')} | {r.get('note', '')[:300]} |")
    md = "\n".join(lines) + "\n"
    print(md)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as f:
            f.write(md)
    sys.exit(0 if any(r["status"] == "ok" for r in results) else 1)


if __name__ == "__main__":
    main()
