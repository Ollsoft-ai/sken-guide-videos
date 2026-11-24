# sken-guide-videos

Short real screen recordings of browser/OS steps for the Olingo Sken onboarding, recorded on real simulators/emulators in GitHub Actions, device UI in Czech.

- **Clips:** `clips/<name>.mp4` (H.264) + `.webm` (VP9) + `.json` (duration, size, labels seen, run link). Portrait, 540 px wide, no audio, ≤ 12 s, ≤ 2 MB, cut to loop.
- **Android** (`.github/workflows/android.yml`): `ubuntu-latest` + KVM, Play Store x86_64 emulator image (Chrome), driven by `adb` + `uiautomator` (`android/run.py`), recorded with `screenrecord`, show-touches on.
- **iOS** (`.github/workflows/ios.yml`): macOS runner, iPhone simulator in cs-CZ, an XCUITest (`ios/GuideUITests`) drives Safari/SpringBoard, recorded with `simctl io recordVideo`; tap circles are drawn in post.
- **Rendering:** `tools/process.py` cuts the raw recording by the marks the drivers log and encodes the clips; `tools/publish.sh` commits them.
- **Run:** Actions → workflow → Run workflow (`workflow_dispatch` only). Raw recordings and per-step screenshots/UI dumps are kept as run artifacts.
- Only public pages of https://app.olingomedical.com/ are opened; no accounts, no secrets.
