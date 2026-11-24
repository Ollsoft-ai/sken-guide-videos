# sken-guide-videos

Short real screen recordings of browser/OS steps for the Olingo Sken onboarding, recorded on real simulators/emulators in GitHub Actions, device UI in Czech.

- **Clips:** `clips/<name>.mp4` (H.264) + `.webm` (VP9) + `.json` (duration, size, labels seen, cut list, run link). Portrait, 540 px wide, no audio, real time (still waits are cut out, never slowed or sped up), ≤ 10 s target, ≤ 2 MB.
- **Tap markers:** a red arrow points at each tap target ~0.75 s before the tap, then a red ring marks the touch; swipes get an arrow along the swipe. Drawn in post (`tools/process.py`), the touch time is snapped to the moment the screen reacts.
- **Android** (`.github/workflows/android.yml`): `ubuntu-latest` + KVM, Play Store x86_64 image (API 37.0, Chrome 145), locale set without root via `android/SetLocale.java`, driven by `adb` + `uiautomator` (`android/run.py`), recorded by the emulator itself (`adb emu screenrecord`; `adb shell screenrecord` leaves out the system permission dialog).
- **iOS** (`.github/workflows/ios.yml`): macOS runner, iPhone 17 / iOS 26.5 simulator in cs-CZ, an XCUITest (`ios/GuideUITests`) drives Safari and SpringBoard, recorded with `simctl io recordVideo`. The arm64 `macos-26` queue often never starts a job; `macos-26-intel` works.
- **Run:** Actions → workflow → Run workflow (`workflow_dispatch` only, `publish` commits the clips). Raw recordings and per-step screenshots/UI dumps stay as run artifacts for 14 days.
- Only public pages of https://app.olingomedical.com/ are opened; no accounts, no secrets.
