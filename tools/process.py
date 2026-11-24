#!/usr/bin/env python3
"""Render looping guide clips from raw recordings.

usage: process.py RAW_DIR OUT_DIR
RAW_DIR holds <clip>.json specs (see clipspec.py) next to the raw recordings.
Writes OUT_DIR/<clip>.mp4 (H.264) and <clip>.webm (VP9), portrait, no audio,
width 540, <= 2 MB, plus OUT_DIR/summary.md. Exits 1 if no clip was rendered.
"""
import glob
import json
import os
import subprocess
import sys

WIDTH = 540
MAX_BYTES = 2_000_000
FPS = 30
TAP_SHOW = 0.45   # seconds a tap circle stays on screen
TAP_DELAY = 0.1   # XCUITest taps land slightly after the mark


def run(cmd):
    print("+", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                          "-show_entries", "stream=width,height:format=duration",
                          "-of", "json", path], check=True, capture_output=True, text=True).stdout
    j = json.loads(out)
    s = j["streams"][0]
    return s["width"], s["height"], float(j["format"]["duration"])


def circle(path, d):
    """Tap marker: grey disc, white ring, thin dark outer ring (visible on light and dark UIs)."""
    r = d / 2
    h = f"hypot(X-{r},Y-{r})"
    white = f"between({h},{r - 11},{r - 5})"
    a = f"if(lt({h},{r - 11}),70,if({white},240,if(lt({h},{r - 2}),140,0)))"
    c = f"if({white},255,40)"
    run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=white:s={d}x{d},format=rgba",
         "-vf", f"geq=r='{c}':g='{c}':b='{c}':a='{a}'", "-frames:v", "1", path])


def graph(spec, w, h, ntaps):
    chains, last = [f"[0:v]setpts=PTS-STARTPTS,fps={FPS}[b0]"], "b0"
    taps = spec.get("taps", [])
    if taps:
        chains.append(f"[1:v]split={len(taps)}" + "".join(f"[c{i}]" for i in range(len(taps))))
        d = round(w * 0.11)
        for i, t in enumerate(taps):
            x, y = round(t["x"] * w - d / 2), round(t["y"] * h - d / 2)
            a, b = t["t"] + TAP_DELAY, t["t"] + TAP_DELAY + TAP_SHOW
            chains.append(f"[{last}][c{i}]overlay=x={x}:y={y}:enable='between(t,{a:.3f},{b:.3f})':shortest=1[o{i}]")
            last = f"o{i}"
    segs = spec["segments"]
    if len(segs) == 1:
        a, b = segs[0]
        chains.append(f"[{last}]trim=start={a}:end={b},setpts=PTS-STARTPTS[cat]")
    else:
        chains.append(f"[{last}]split={len(segs)}" + "".join(f"[s{i}]" for i in range(len(segs))))
        for i, (a, b) in enumerate(segs):
            chains.append(f"[s{i}]trim=start={a}:end={b},setpts=PTS-STARTPTS[t{i}]")
        chains.append("".join(f"[t{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=0[cat]")
    chains.append(f"[cat]scale={WIDTH}:-2:flags=lanczos,format=yuv420p[out]")
    return ";".join(chains)


def render(spec, raw_dir, out_dir, tmp):
    raw = os.path.join(raw_dir, spec["raw"])
    w, h, _ = probe(raw)
    inputs = ["-i", raw]
    if spec.get("taps"):
        cpath = os.path.join(tmp, f"circle-{w}.png")
        if not os.path.exists(cpath):
            circle(cpath, round(w * 0.11))
        inputs += ["-loop", "1", "-i", cpath]
    fg = graph(spec, w, h, len(spec.get("taps", [])))
    name = spec["clip"]
    mp4 = os.path.join(out_dir, name + ".mp4")
    for crf in (24, 27, 30, 33, 36):
        run(["ffmpeg", "-y", "-loglevel", "error", *inputs, "-filter_complex", fg, "-map", "[out]", "-an",
             "-c:v", "libx264", "-preset", "slow", "-crf", str(crf), "-profile:v", "high",
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
    ow, oh, dur = probe(mp4)
    res = {"clip": name, "status": "ok", "mp4": os.path.basename(mp4), "webm": os.path.basename(webm),
            "size_mp4": os.path.getsize(mp4), "size_webm": os.path.getsize(webm),
            "duration": round(dur, 2), "width": ow, "height": oh, "note": spec.get("note", ""),
           "run": os.environ.get("GUIDE_RUN_URL", "")}
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
