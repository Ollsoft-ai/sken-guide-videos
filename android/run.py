#!/usr/bin/env python3
"""Record the Android Chrome guide clips on an already booted emulator (adb on PATH).

Env: GUIDE_URL, GUIDE_CLIPS (comma list of camera,install), GUIDE_OUT (default ./out).
Writes OUT/raw/<clip>.raw.mp4 + <clip>.json (spec for tools/process.py) and
OUT/debug/<clip>/NN-*.png|xml (screenshots + UI dumps of every step).
"""
import glob
import os
import re
import subprocess
import sys
import time
import traceback
import unicodedata
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import clipspec  # noqa: E402

URL = os.environ.get("GUIDE_URL", "https://app.olingomedical.com/")
CLIPS = [c.strip() for c in os.environ.get("GUIDE_CLIPS", "camera,install").split(",") if c.strip()]
OUT = os.path.abspath(os.environ.get("GUIDE_OUT", os.path.join(HERE, "..", "out")))
RAW, DBG = os.path.join(OUT, "raw"), os.path.join(OUT, "debug")
CHROME = "com.android.chrome"
TARGET = r"Načíst kód z počítače"
DISMISS = [
    r"^(Přijmout a pokračovat|Accept & continue|Accept and continue)$",
    r"^(Používat bez účtu|Pokračovat bez účtu|Use without an account)$",
    r"^(Ne,? děkuji|Ne,? díky|No,? thanks|Teď ne|Nyní ne|Not now|Přeskočit|Skip)$",
    r"^(Rozumím|Got it|Zavřít|Close|Počkat|Wait|Pokračovat|Continue|OK)$",
]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def run(cmd, timeout=60, binary=False):
    try:
        if binary:
            return subprocess.run(cmd, capture_output=True, timeout=timeout)
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        log("TIMEOUT", " ".join(cmd))
        return subprocess.CompletedProcess(cmd, 124, b"" if binary else "", b"" if binary else "timeout")


def adb(*a, **kw):
    return run(["adb", *a], **kw)


def sh(cmd, **kw):
    return adb("shell", cmd, **kw).stdout.strip()


def norm(s):
    """NFC + plain spaces: the app uses no-break spaces (e.g. „z\u00a0počítače“)."""
    return re.sub(r"[\u00a0\u202f\u2007]", " ", unicodedata.normalize("NFC", s or ""))


class Node:
    def __init__(self, e):
        self.text = norm(e.get("text"))
        self.desc = norm(e.get("content-desc"))
        self.rid = e.get("resource-id") or ""
        self.cls = e.get("class") or ""
        self.pkg = e.get("package") or ""
        self.clickable = e.get("clickable") == "true"
        b = [int(v) for v in re.findall(r"-?\d+", e.get("bounds") or "[0,0][0,0]")[:4]]
        self.x1, self.y1, self.x2, self.y2 = b
        self.center = ((self.x1 + self.x2) // 2, (self.y1 + self.y2) // 2)

    def label(self):
        return self.text or self.desc


class Debug:
    def __init__(self):
        self.dir, self.n = DBG, 0

    def clip(self, name):
        self.dir, self.n = os.path.join(DBG, name), 0
        os.makedirs(self.dir, exist_ok=True)

    def shot(self, tag, xml=None):
        self.n += 1
        base = os.path.join(self.dir, f"{self.n:02d}-{tag}")
        png = adb("exec-out", "screencap -p", binary=True, timeout=30).stdout
        if png:
            open(base + ".png", "wb").write(png)
        if xml:
            open(base + ".xml", "w").write(xml)


dbg = Debug()


def dump(tag=None):
    for _ in range(3):
        r = adb("shell", "uiautomator dump /sdcard/ui.xml", timeout=45)
        if "dumped" in (r.stdout + r.stderr):
            xml = adb("exec-out", "cat /sdcard/ui.xml").stdout
            try:
                nodes = [Node(e) for e in ET.fromstring(xml).iter("node")]
            except ET.ParseError:
                continue
            labels = [n.label() for n in nodes if n.label()]
            log("screen:", " | ".join(labels)[:700])
            if tag:
                dbg.shot(tag, xml)
            return nodes
        log("dump failed:", (r.stdout + r.stderr).strip()[:200])
        time.sleep(0.7)
    if tag:
        dbg.shot(tag + "-nodump")
    return []


def find(nodes, pat, fields=("text", "desc"), pkg=None, not_pkg=None):
    rx = re.compile(pat, re.I)
    hits = [n for n in nodes if any(rx.search(getattr(n, f) or "") for f in fields)
            and (pkg is None or n.pkg == pkg) and (not_pkg is None or n.pkg != not_pkg)
            and n.x2 > n.x1 and n.y2 > n.y1]
    hits.sort(key=lambda n: not n.clickable)
    return hits[0] if hits else None


def open_xml():
    return adb("exec-out", "cat /sdcard/ui.xml").stdout


def wait_for(pat, timeout=10, tag=None, **kw):
    end = time.time() + timeout
    while True:
        nodes = dump()
        hit = find(nodes, pat, **kw)
        if hit or time.time() > end:
            if tag:
                dbg.shot(tag, open_xml())
            return hit
        time.sleep(0.3)


SCREEN = [1080, 2400]


def tap(target, marks=None, name=None):
    """Tap; with marks, log a timed mark carrying the normalized position (tap circle in post)."""
    x, y = target.center if isinstance(target, Node) else target
    log("tap", x, y, target.label() if isinstance(target, Node) else "")
    if marks is not None:
        marks.add(name, x=round(x / SCREEN[0], 4), y=round(y / SCREEN[1], 4))
    sh(f"input tap {x} {y}")


def media_duration(path):
    d = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]).stdout.strip()
    try:
        return float(d)
    except ValueError:  # streamed webm without a duration header: take the last packet time
        pts = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time",
                   "-of", "csv=p=0", path], timeout=120).stdout.split()
        vals = [float(v) for v in pts if re.match(r"^[0-9.]+$", v)]
        return max(vals) if vals else 0.0


class Recorder:
    """Emulator-side recording (`adb emu screenrecord`): it sees the composited screen,
    including Android's camera permission dialog, which `adb shell screenrecord` leaves
    out (the dialog is missing from device recordings). Device screenrecord is the fallback."""

    def start(self, clip):
        self.clip, self.emu = clip, True
        self.raw = os.path.join(RAW, clip + ".raw.webm")
        if os.path.exists(self.raw):
            os.remove(self.raw)
        self.t_start = time.time()
        r = adb("emu", "screenrecord", "start", "--time-limit", "170", "--fps", "30", "--bit-rate", "8000000", self.raw)
        out = (r.stdout + r.stderr).strip()
        log("emu screenrecord:", out[:200])
        if "KO" in out or r.returncode:
            self.t_start = time.time()
            r = adb("emu", "screenrecord", "start", self.raw)
            log("emu screenrecord (plain):", (r.stdout + r.stderr).strip()[:200])
        time.sleep(2.5)
        if not os.path.exists(self.raw):
            log("emulator recorder wrote no file -> adb shell screenrecord")
            adb("emu", "screenrecord", "stop")
            self.emu = False
            self.raw = os.path.join(RAW, clip + ".raw.mp4")
            sh("rm -f /sdcard/rec.mp4")
            self.p = subprocess.Popen(["adb", "shell", "screenrecord", "--bit-rate", "8000000", "/sdcard/rec.mp4"],
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            self.t_start = time.time() + 1.0
            time.sleep(2.5)
        log("recording", self.clip, "emu" if self.emu else "screenrecord")

    def stop(self):
        t_stop = time.time()
        if self.emu:
            log("emu stop:", (adb("emu", "screenrecord", "stop").stdout or "").strip()[:200])
            last = -1
            for _ in range(40):
                size = os.path.getsize(self.raw) if os.path.exists(self.raw) else -1
                if size == last and size > 0:
                    break
                last = size
                time.sleep(1)
        else:
            sh("kill -2 $(pidof screenrecord)")
            try:
                self.p.wait(timeout=20)
            except subprocess.TimeoutExpired:
                self.p.kill()
            time.sleep(1.5)
            adb("pull", "/sdcard/rec.mp4", self.raw, timeout=120)
        self.t0 = self.t_start
        if os.path.exists(self.raw):
            dur = media_duration(self.raw)
            log(f"raw {self.raw}: {os.path.getsize(self.raw)} B, duration {dur:.2f}, wall since start {t_stop - self.t_start:.2f}")
            if self.emu and dur > 1:
                self.t0 = t_stop - dur  # constant frame rate: the last frame is the stop time
        return os.path.basename(self.raw)


rec = Recorder()


class Marks(list):
    def add(self, name, **kw):
        self.append({"name": name, "t": time.time(), **kw})
        log("MARK", name)


def finish(clip, marks, status, note):
    raw = rec.stop()
    spec = clipspec.build(clip, raw, marks, rec.t0, status=status, note=note, overlay_taps=True)
    clipspec.write(os.path.join(RAW, clip + ".json"), spec)
    log("RESULT", clip, status, note)


def fail(clip, note):
    clipspec.write(os.path.join(RAW, clip + ".json"), {"clip": clip, "status": "failed", "note": note, "segments": []})
    log("RESULT", clip, "failed", note)


# ---------- device setup ----------

def set_locale(tag="cs-CZ"):
    log("locale before:", sh("getprop persist.sys.locale"), sh("settings get system system_locales"))
    if sh("getprop persist.sys.locale").lower().startswith("cs"):
        return
    sdk = os.environ.get("ANDROID_HOME") or os.environ.get("ANDROID_SDK_ROOT")
    jar = sorted(glob.glob(f"{sdk}/platforms/android-*/android.jar"))[-1]
    d8 = sorted(glob.glob(f"{sdk}/build-tools/*/d8"))[-1]
    build = os.path.join(OUT, ".build")
    os.makedirs(build, exist_ok=True)
    for cmd in (["javac", "--release", "11", "-cp", jar, "-d", build, os.path.join(HERE, "SetLocale.java")],
                [d8, "--min-api", "26", "--lib", jar, "--output", build, os.path.join(build, "SetLocale.class")]):
        r = run(cmd, timeout=180)
        log(" ".join(cmd[:2]), r.returncode, (r.stdout + r.stderr).strip()[-400:])
    adb("push", os.path.join(build, "classes.dex"), "/data/local/tmp/setlocale.dex")
    log(sh(f"CLASSPATH=/data/local/tmp/setlocale.dex app_process / SetLocale {tag} 2>&1"))
    time.sleep(4)
    log("locale after:", sh("getprop persist.sys.locale"))


def setup():
    adb("wait-for-device", timeout=300)
    while sh("getprop sys.boot_completed") != "1":
        time.sleep(2)
    log("device:", sh("getprop ro.product.model"), "Android", sh("getprop ro.build.version.release"),
        "SDK", sh("getprop ro.build.version.sdk"), sh("getprop ro.build.type"))
    log("chrome:", sh(f"dumpsys package {CHROME} | grep -m1 versionName"))
    set_locale()
    sh("settings put system show_touches 0")  # tap circles are drawn in post, same as iOS
    m = re.search(r"(\d+)x(\d+)", sh("wm size"))
    if m:
        SCREEN[:] = [int(m.group(1)), int(m.group(2))]
    sh("svc power stayon true")
    sh("settings put system screen_off_timeout 1800000")
    sh("input keyevent KEYCODE_WAKEUP")
    sh("wm dismiss-keyguard")
    # Clean status bar (demo mode): fixed time, full battery/wifi, no notification icons.
    sh("settings put global sysui_demo_allowed 1")
    for extra in ("-e command enter", "-e command clock -e hhmm 0941",
                  "-e command battery -e level 100 -e plugged false",
                  "-e command network -e wifi show -e level 4 -e fully true",
                  "-e command network -e mobile hide",
                  "-e command notifications -e visible false"):
        sh(f"am broadcast -a com.android.systemui.demo {extra}")
    # Chrome reads this flags file on user builds only when it is the debug app.
    sh("am set-debug-app --persistent " + CHROME)
    sh("echo '_ --disable-fre --no-default-browser-check --no-first-run --disable-search-engine-choice-screen' "
       "> /data/local/tmp/chrome-command-line; chmod 644 /data/local/tmp/chrome-command-line")
    os.makedirs(os.path.join(DBG, "setup"), exist_ok=True)
    dbg.clip("setup")
    dump("home")


def reset_chrome():
    sh(f"am force-stop {CHROME}")
    sh(f"pm clear {CHROME}")
    sh(f"pm grant {CHROME} android.permission.POST_NOTIFICATIONS")
    sh("input keyevent KEYCODE_HOME")
    time.sleep(1)


def open_page(timeout=120):
    sh(f"am start -a android.intent.action.VIEW -d {URL} -n {CHROME}/com.google.android.apps.chrome.Main")
    end, i = time.time() + timeout, 0
    while time.time() < end:
        i += 1
        nodes = dump(f"settle-{i}" if i <= 25 else None)
        t = find(nodes, TARGET)
        if t:
            return t
        if find(nodes, r"vyhledávač|search engine", fields=("text",)):
            g = find(nodes, r"^Google")
            if g:
                tap(g)
                time.sleep(0.8)
                s = find(dump(), r"^(Nastavit jako výchozí|Set as default)$")
                if s:
                    tap(s)
                    time.sleep(2)
                    continue
        hit = None
        for pat in DISMISS:
            hit = find(nodes, pat, fields=("text",))
            if hit:
                break
        if hit:
            log("dismiss:", hit.label())
            tap(hit)
            time.sleep(1.5)
        else:
            time.sleep(1.5)
    return None


# ---------- clips ----------

def clip_camera():
    clip = "android-chrome-camera"
    dbg.clip(clip)
    reset_chrome()
    btn = open_page()
    if not btn:
        return fail(clip, "page/button did not appear")
    time.sleep(2)
    btn = find(dump("before"), TARGET) or btn
    rec.start(clip)
    m = Marks()
    tap(btn, m, "tap_open")
    allow = wait_for(r"^Povolit při|^Allow (on every visit|while visiting)", timeout=15, tag="prompt", pkg=CHROME)
    allow = allow or find(dump("prompt-2"), r"^(Povolit|Allow)", pkg=CHROME)
    if not allow:
        m.add("end")
        return finish(clip, m, "failed", "no Chrome camera prompt within 15 s")
    prompt_texts = " | ".join(n.label() for n in dump() if n.label() and n.pkg == CHROME and n.y1 > 0)[:400]
    time.sleep(0.9)
    tap(allow, m, "tap_allow")
    os_allow = wait_for(r"permission_allow_foreground_only_button|Při používání aplikace|While using the app",
                        timeout=5, tag="os-prompt", fields=("rid", "text"))
    note = "site prompt: " + prompt_texts
    if os_allow:
        time.sleep(0.9)
        tap(os_allow, m, "tap_os_allow")
        note += " / OS prompt: " + os_allow.label()
    time.sleep(2.8)
    dbg.shot("live")
    m.add("end")
    finish(clip, m, "ok", note)


INSTALL_ITEM = (r"^(Instalovat aplikaci|Nainstalovat aplikaci|Přidat na plochu|Přidat na domovskou obrazovku|"
                r"Install app|Add to (the )?home screen)")


def clip_install():
    clip = "android-chrome-install"
    dbg.clip(clip)
    reset_chrome()
    if not open_page():
        return fail(clip, "page/button did not appear")
    time.sleep(4)  # let Chrome check the manifest + service worker
    nodes = dump("before")
    menu = find(nodes, r"menu_button$", fields=("rid",)) or find(nodes, r"^(Další možnosti|Možnosti|More options)", fields=("desc",))
    if not menu:
        return fail(clip, "Chrome menu button not found")
    rec.start(clip)
    m = Marks()
    tap(menu, m, "tap_menu")
    item = wait_for(INSTALL_ITEM, timeout=6, tag="menu", pkg=CHROME)
    if not item:
        sh("input swipe 700 1800 700 700 300")
        item = wait_for(INSTALL_ITEM, timeout=4, tag="menu-scrolled", pkg=CHROME)
    if not item:
        m.add("end")
        return finish(clip, m, "failed", "install item not in Chrome menu")
    note = "menu item: " + item.label()
    time.sleep(0.8)
    tap(item, m, "tap_item")
    btn = wait_for(r"^(Instalovat|Nainstalovat|Install|Přidat|Add)$", timeout=10, tag="dialog")
    dialog_texts = " | ".join(n.label() for n in dump() if n.label())[:400]
    note += " / dialog: " + dialog_texts
    if not btn:
        m.add("end")
        return finish(clip, m, "failed", note + " / no install button")
    time.sleep(1.0)
    t_install = time.time()
    tap(btn, m, "tap_install")
    done, extra = None, ""
    end = time.time() + 60
    while time.time() < end:
        pkgs = sh("pm list packages | grep -i webapk")
        if pkgs:
            done = "webapk " + pkgs.replace("package:", "")
            break
        nodes = dump()
        confirm = find(nodes, r"^(Přidat na plochu|Přidat automaticky|Přidat|Add to home screen|Add automatically|Add)$",
                       fields=("text",), not_pkg=CHROME)
        if confirm:
            dbg.shot("launcher-confirm")
            tap(confirm, m, "tap_confirm")
            extra = " / launcher confirm: " + confirm.label()
            time.sleep(2)
            done = "shortcut"
            break
        if find(nodes, r"přidán|nainstalován|added|installed", fields=("text",)):
            done = "toast"
            break
        time.sleep(1)
    dbg.shot("after-install")
    note += extra + f" / result: {done} after {time.time() - t_install:.1f}s"
    if time.time() - t_install > 4:
        m.append({"name": "cut_from", "t": t_install + 1.8})  # drop the wait while Chrome installs
        m.add("cut_to")
    time.sleep(1.2)
    sh("input keyevent KEYCODE_HOME")
    time.sleep(2.5)
    nodes = dump("home")
    icon = find(nodes, r"^Olingo Sken$")
    if not icon:
        sh("input swipe 900 1300 150 1300 250")  # next home page
        time.sleep(1.8)
        icon = find(dump("home-2"), r"^Olingo Sken$")
    note += " / icon on home: " + ("yes" if icon else "NOT FOUND")
    time.sleep(1.0)
    m.add("end")
    finish(clip, m, "ok" if (done and icon) else "failed", note)


def main():
    os.makedirs(RAW, exist_ok=True)
    os.makedirs(DBG, exist_ok=True)
    setup()
    for c in CLIPS:
        fn = {"camera": clip_camera, "install": clip_install}.get(c)
        if not fn:
            log("unknown clip", c)
            continue
        try:
            fn()
        except Exception:
            traceback.print_exc()
            try:
                rec.stop()
            except Exception:
                pass
            fail(f"android-chrome-{c}", "exception: " + traceback.format_exc()[-300:])
    sh(f"am force-stop {CHROME}")


if __name__ == "__main__":
    main()
