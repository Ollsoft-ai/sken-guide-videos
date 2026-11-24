#!/usr/bin/env python3
"""Record the iOS Safari guide clips on a Czech-language simulator (macOS runner).

Env: GUIDE_URL, GUIDE_CLIPS (comma list of camera,add-to-home), IOS_DEVICE (default "iPhone 17"),
IOS_RUNTIME (version prefix, default newest), GUIDE_OUT (default ./out). Needs xcodegen.
Writes OUT/raw/<clip>.raw.mp4 + <clip>.json and OUT/debug/<clip>/ (screenshots, UI trees, xcodebuild log).
"""
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import clipspec  # noqa: E402

URL = os.environ.get("GUIDE_URL", "https://app.olingomedical.com/")
CLIPS = [c.strip() for c in os.environ.get("GUIDE_CLIPS", "camera,add-to-home").split(",") if c.strip()]
DEVICE = os.environ.get("IOS_DEVICE") or "iPhone 17"
RUNTIME = os.environ.get("IOS_RUNTIME") or ""
OUT = os.path.abspath(os.environ.get("GUIDE_OUT", os.path.join(ROOT, "out")))
RAW, DBG = os.path.join(OUT, "raw"), os.path.join(OUT, "debug")
DD = os.path.join(ROOT, "build", "dd")
PROJECT = os.path.join(HERE, "SkenGuide.xcodeproj")
TESTS = {"camera": ("ios-safari-camera", "testCamera"),
         "add-to-home": ("ios-safari-add-to-home", "testAddToHome")}


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def sh(cmd, check=True, timeout=None, env=None, logfile=None):
    log("+", " ".join(cmd))
    if logfile:
        with open(logfile, "w") as f:
            r = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT, text=True, timeout=timeout, env=env)
    else:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    if check and r.returncode:
        tail = open(logfile).read()[-4000:] if logfile else (r.stdout + r.stderr)[-4000:]
        raise SystemExit(f"command failed ({r.returncode}): {' '.join(cmd)}\n{tail}")
    return r


def simctl(*a, **kw):
    return sh(["xcrun", "simctl", *a], **kw)


def create_sim():
    rts = json.loads(simctl("list", "runtimes", "-j").stdout)["runtimes"]
    rts = [r for r in rts if r.get("platform") == "iOS" and r.get("isAvailable")]
    if RUNTIME:
        rts = [r for r in rts if r["version"].startswith(RUNTIME)]
    rts.sort(key=lambda r: [int(x) for x in re.findall(r"\d+", r["version"])])
    rt = rts[-1]
    types = [d for d in rt.get("supportedDeviceTypes", []) if d.get("productFamily") == "iPhone"]
    dt = next((d for d in types if d["name"] == DEVICE), types[-1])
    log("runtime", rt["name"], rt["identifier"], "device", dt["name"])
    udid = simctl("create", "guide-cs", dt["identifier"], rt["identifier"]).stdout.strip()
    return udid, rt["name"], dt["name"]


def boot(udid):
    simctl("boot", udid)
    simctl("bootstatus", udid, "-b", timeout=900)


def prepare(udid):
    boot(udid)
    simctl("spawn", udid, "defaults", "write", "-g", "AppleLanguages", "-array", "cs-CZ")
    simctl("spawn", udid, "defaults", "write", "-g", "AppleLocale", "-string", "cs_CZ")
    simctl("shutdown", udid)
    boot(udid)
    log("languages:", simctl("spawn", udid, "defaults", "read", "-g", "AppleLanguages", check=False).stdout.split())
    simctl("status_bar", udid, "override", "--time", "9:41", "--dataNetwork", "wifi", "--wifiMode", "active",
           "--wifiBars", "3", "--cellularMode", "active", "--cellularBars", "4", "--batteryState", "charged",
           "--batteryLevel", "100", check=False)
    time.sleep(10)  # let SpringBoard settle after the first boot in Czech


def build(udid):
    sh(["xcodegen", "generate", "--spec", os.path.join(HERE, "project.yml")])
    os.makedirs(DBG, exist_ok=True)
    sh(["xcodebuild", "build-for-testing", "-project", PROJECT, "-scheme", "GuideUITests",
        "-destination", f"id={udid}", "-derivedDataPath", DD], timeout=1800,
       logfile=os.path.join(DBG, "build.log"))


class Recording:
    def __init__(self, udid, path):
        self.p = subprocess.Popen(["xcrun", "simctl", "io", udid, "recordVideo", "--codec=h264", "--force", path],
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.t0, t_launch = None, time.time()
        threading.Thread(target=self._read, daemon=True).start()
        while self.t0 is None and time.time() - t_launch < 15:
            time.sleep(0.05)
        if self.t0 is None:
            self.t0 = t_launch + 1.0
            log("no 'Recording started' line; estimating t0")

    def _read(self):
        for line in self.p.stdout:
            log("rec:", line.strip())
            if self.t0 is None and re.search(r"start", line, re.I):
                self.t0 = time.time()

    def stop(self):
        self.p.send_signal(signal.SIGINT)
        try:
            self.p.wait(timeout=60)
        except subprocess.TimeoutExpired:
            self.p.kill()


def record(udid, key):
    clip, test = TESTS[key]
    out = os.path.join(DBG, clip)
    os.makedirs(out, exist_ok=True)
    raw = os.path.join(RAW, clip + ".raw.mp4")
    simctl("terminate", udid, "com.apple.mobilesafari", check=False)
    simctl("openurl", udid, URL)
    time.sleep(8)
    rec = Recording(udid, raw)
    env = dict(os.environ, TEST_RUNNER_GUIDE_OUT=out, TEST_RUNNER_GUIDE_URL=URL)
    logfile = os.path.join(out, "xcodebuild.log")
    r = sh(["xcodebuild", "test-without-building", "-project", PROJECT, "-scheme", "GuideUITests",
            "-destination", f"id={udid}", "-derivedDataPath", DD,
            f"-only-testing:GuideUITests/SafariGuideTests/{test}"],
           check=False, timeout=900, env=env, logfile=logfile)
    time.sleep(0.5)
    rec.stop()
    for line in open(logfile, errors="replace"):
        if "GUIDE " in line or "error:" in line or "Test Case" in line:
            log("  |", line.rstrip()[:400])
    marks_file = os.path.join(out, clip + ".marks.json")
    if os.path.exists(marks_file):
        m = json.load(open(marks_file))
        spec = clipspec.build(clip, os.path.basename(raw), m["marks"], rec.t0, status=m["status"],
                              note=m["note"], overlay_taps=True)
    else:
        spec = {"clip": clip, "status": "failed", "segments": [],
                "note": f"test wrote no marks (xcodebuild exit {r.returncode})"}
    clipspec.write(os.path.join(RAW, clip + ".json"), spec)
    log("RESULT", clip, spec["status"], spec["note"][:500])


def main():
    os.makedirs(RAW, exist_ok=True)
    os.makedirs(DBG, exist_ok=True)
    udid, rt, dev = create_sim()
    prepare(udid)
    build(udid)
    with open(os.path.join(DBG, "device.txt"), "w") as f:
        f.write(f"{dev} / {rt} / {udid}\n")
    for key in CLIPS:
        if key not in TESTS:
            log("unknown clip", key)
            continue
        try:
            record(udid, key)
        except Exception as e:  # keep the other clips going
            log("ERROR", key, e)
            clip = TESTS[key][0]
            clipspec.write(os.path.join(RAW, clip + ".json"),
                           {"clip": clip, "status": "failed", "segments": [], "note": f"exception: {e}"})


if __name__ == "__main__":
    main()
