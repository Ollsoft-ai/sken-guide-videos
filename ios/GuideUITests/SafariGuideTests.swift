import XCTest

/// Drives Safari and SpringBoard on the simulator for the guide clips.
/// ios/run.py opens the page with `simctl openurl`, records the screen, and reads
/// GUIDE_OUT/<clip>.marks.json (timed marks) plus NN-*.png/.txt debug dumps written here.
final class SafariGuideTests: XCTestCase {
    let safari = XCUIApplication(bundleIdentifier: "com.apple.mobilesafari")
    let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
    let env = ProcessInfo.processInfo.environment
    var marks: [[String: Any]] = []
    var step = 0

    var outDir: URL { URL(fileURLWithPath: env["GUIDE_OUT"] ?? NSTemporaryDirectory()) }
    var pageURL: URL { URL(string: env["GUIDE_URL"] ?? "https://app.olingomedical.com/")! }

    override func setUp() {
        super.setUp()
        continueAfterFailure = true
        try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
    }

    // MARK: - helpers

    func log(_ s: String) { print("GUIDE \(s)") }

    func mark(_ name: String, _ el: XCUIElement? = nil) {
        var m: [String: Any] = ["name": name, "t": Date().timeIntervalSince1970]
        if let el = el, el.exists {
            let f = el.frame
            let s = springboard.frame
            if s.width > 0 && s.height > 0 {
                m["x"] = Double((f.midX - s.minX) / s.width)
                m["y"] = Double((f.midY - s.minY) / s.height)
            }
        }
        marks.append(m)
        log("MARK \(name)")
    }

    func snap(_ name: String, _ app: XCUIApplication? = nil) {
        step += 1
        let base = String(format: "%02d-", step) + name
        let png = XCUIScreen.main.screenshot().pngRepresentation
        try? png.write(to: outDir.appendingPathComponent(base + ".png"))
        let target = app ?? (safari.state == .runningForeground ? safari : springboard)
        try? target.debugDescription.write(to: outDir.appendingPathComponent(base + ".txt"), atomically: true, encoding: .utf8)
    }

    func save(_ clip: String, _ status: String, _ note: String) {
        let obj: [String: Any] = ["clip": clip, "status": status, "note": note, "marks": marks]
        if let data = try? JSONSerialization.data(withJSONObject: obj, options: [.prettyPrinted]) {
            try? data.write(to: outDir.appendingPathComponent(clip + ".marks.json"))
        }
        log("RESULT \(clip) \(status) \(note)")
    }

    /// First hittable element whose label or identifier is one of `names`.
    func find(_ names: [String], in apps: [XCUIApplication], timeout: TimeInterval = 5) -> XCUIElement? {
        let pred = NSPredicate(format: "label IN %@ OR identifier IN %@", names, names)
        let end = Date().addingTimeInterval(timeout)
        repeat {
            for app in apps {
                let q = app.descendants(matching: .any).matching(pred)
                let n = min(q.count, 8)
                for i in 0..<n {
                    let e = q.element(boundBy: i)
                    if e.exists && e.isHittable { return e }
                }
            }
            usleep(250_000)
        } while Date() < end
        return nil
    }

    /// The page's „Načíst kód z počítače“ button once the page is loaded in Safari.
    func pageButton() -> XCUIElement? {
        safari.activate()
        let btn = safari.buttons.matching(NSPredicate(format: "label CONTAINS[c] %@", "Načíst kód")).firstMatch
        if btn.waitForExistence(timeout: 30) { sleep(2); return btn }
        snap("page-timeout-1", safari)
        XCUIDevice.shared.system.open(pageURL)
        if btn.waitForExistence(timeout: 30) { sleep(2); return btn }
        snap("page-timeout-2", safari)
        return nil
    }

    /// Share button (bottom layout) or the „⋯“ page menu (iOS 26 compact layout).
    func menuButton() -> XCUIElement? {
        if let b = find(["ShareButton", "Sdílet", "Share"], in: [safari], timeout: 1) { return b }
        if let b = find(["Více", "Další", "Další možnosti", "Nabídka", "Nabídka stránky", "Možnosti stránky",
                         "More", "MoreMenuButton", "PageMenuButton"], in: [safari], timeout: 1) { return b }
        // Fallback: right-most small button in the bottom toolbar.
        let s = springboard.frame
        var best: XCUIElement? = nil
        var bestX: CGFloat = -1
        for b in safari.buttons.allElementsBoundByIndex where b.exists {
            let f = b.frame
            if f.midY > s.height * 0.82 && f.midX > s.width * 0.6 && f.width < s.width * 0.3 && f.midX > bestX && b.isHittable {
                best = b
                bestX = f.midX
            }
        }
        return best
    }

    func describe(_ e: XCUIElement) -> String { "'\(e.label)' [\(e.identifier)]" }

    /// The XCUITest runner app sits on the home screen right where the new web clip would land.
    /// Move it off the home screen (kept in the App Library; never delete it, it runs this test).
    func hideRunnerIcon() -> String {
        XCUIDevice.shared.press(.home)
        sleep(1)
        let icon = springboard.icons.matching(NSPredicate(format: "label BEGINSWITH 'GuideUITests' OR label BEGINSWITH 'GuideHost'")).firstMatch
        if !icon.exists { return "runner icon not on home screen" }
        var swipes = 0
        while !icon.isHittable && swipes < 4 {
            springboard.swipeLeft()
            sleep(1)
            swipes += 1
        }
        icon.press(forDuration: 1.5)
        snap("runner-menu", springboard)
        let remove = springboard.buttons.matching(NSPredicate(format: "label BEGINSWITH[c] 'Odstranit' OR label BEGINSWITH[c] 'Remove'")).firstMatch
        guard remove.waitForExistence(timeout: 4) else { XCUIDevice.shared.press(.home); return "no remove item" }
        remove.tap()
        let safe = NSPredicate(format: "(label CONTAINS[c] 'plochy' OR label CONTAINS[c] 'knihovn' OR label CONTAINS[c] 'Home Screen' OR label CONTAINS[c] 'App Library') AND NOT (label BEGINSWITH[c] 'Smazat' OR label BEGINSWITH[c] 'Delete')")
        let fromHome = springboard.buttons.matching(safe).firstMatch
        guard fromHome.waitForExistence(timeout: 4) else {
            snap("runner-alert", springboard)
            let cancel = springboard.buttons.matching(NSPredicate(format: "label IN %@", ["Zrušit", "Cancel"])).firstMatch
            if cancel.exists { cancel.tap() }
            return "no remove-from-home button"
        }
        let label = fromHome.label
        fromHome.tap()
        sleep(1)
        snap("runner-hidden", springboard)
        return "runner icon removed via '\(label)'"
    }

    // MARK: - clips

    func testCamera() {
        let clip = "ios-safari-camera"
        guard let btn = pageButton() else { save(clip, "failed", "page did not load"); return }
        snap("before", safari)
        mark("tap_open", btn)
        btn.tap()
        var alert: XCUIElement? = nil
        let end = Date().addingTimeInterval(12)
        while Date() < end && alert == nil {
            if safari.alerts.firstMatch.exists {
                alert = safari.alerts.firstMatch
            } else if springboard.alerts.firstMatch.exists {
                alert = springboard.alerts.firstMatch
            } else {
                usleep(200_000)
            }
        }
        guard let a = alert else {
            sleep(2)
            snap("no-prompt", safari)
            let texts = safari.webViews.staticTexts.allElementsBoundByIndex.prefix(40).map { $0.label }.filter { !$0.isEmpty }
            mark("end")
            save(clip, "not-possible", "no camera permission prompt within 12 s after the tap; page shows: " + texts.joined(separator: " | "))
            return
        }
        let title = a.label
        let buttons = a.buttons.allElementsBoundByIndex.map { $0.label }
        snap("prompt", safari)
        mark("prompt")
        sleep(1)
        let allow = a.buttons.matching(NSPredicate(format: "label IN %@", ["Povolit", "Allow"])).firstMatch
        let target = allow.exists ? allow : a.buttons.element(boundBy: max(0, a.buttons.count - 1))
        mark("tap_allow", target)
        target.tap()
        sleep(3)
        snap("after-allow", safari)
        mark("end")
        save(clip, "ok", "alert: \(title) | buttons: \(buttons.joined(separator: " / "))")
    }

    func testAddToHome() {
        let clip = "ios-safari-add-to-home"
        let a2hsNames = ["Přidat na plochu", "Přidat na plochu…", "Add to Home Screen"]
        var notes: [String] = [hideRunnerIcon()]
        guard pageButton() != nil else { save(clip, "failed", "page did not load"); return }
        snap("before", safari)
        guard let menu = menuButton() else {
            snap("no-menu", safari)
            save(clip, "failed", "Safari share/menu button not found")
            return
        }
        notes.append("menu \(describe(menu))")
        mark("tap_menu", menu)
        menu.tap()
        sleep(1)
        snap("menu-open", safari)
        var a2hs = find(a2hsNames, in: [safari], timeout: 1.5)
        if a2hs == nil, let share = find(["Sdílet", "Sdílet…", "Share", "Share…"], in: [safari], timeout: 3) {
            notes.append("share \(describe(share))")
            mark("tap_share", share)
            share.tap()
            sleep(2)
            snap("share-sheet", safari)
            a2hs = find(a2hsNames, in: [safari, springboard], timeout: 1)
            if a2hs == nil, let more = find(["Zobrazit další", "Zobrazit více", "Show More"], in: [safari], timeout: 2) {
                notes.append("more \(describe(more))")
                mark("tap_more", more)
                more.tap()
                sleep(1)
                snap("share-more", safari)
                a2hs = find(a2hsNames, in: [safari, springboard], timeout: 3)
            }
            var scrolls = 0
            while a2hs == nil && scrolls < 2 {
                scrolls += 1
                let from = safari.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.88))
                let to = safari.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.45))
                from.press(forDuration: 0.05, thenDragTo: to)
                sleep(1)
                snap("share-scrolled-\(scrolls)", safari)
                a2hs = find(a2hsNames, in: [safari, springboard], timeout: 2)
            }
        }
        guard let add = a2hs else {
            snap("no-a2hs", safari)
            save(clip, "failed", notes.joined(separator: "; ") + "; 'Přidat na plochu' not found")
            return
        }
        notes.append("a2hs \(describe(add))")
        mark("tap_a2hs", add)
        add.tap()
        sleep(2)
        snap("a2hs-sheet", safari)
        guard let confirm = find(["Přidat", "Add"], in: [safari], timeout: 6) else {
            save(clip, "failed", notes.joined(separator: "; ") + "; 'Přidat' button not found")
            return
        }
        // The sheet loads the icon and name first; „Přidat“ stays disabled until then.
        let ready = Date().addingTimeInterval(20)
        while !confirm.isEnabled && Date() < ready { usleep(300_000) }
        sleep(1)
        snap("a2hs-ready", safari)
        mark("tap_add", confirm)
        confirm.tap()
        let icon = springboard.icons["Olingo Sken"]
        var onHome = icon.waitForExistence(timeout: 8) && icon.isHittable
        if !onHome {
            snap("after-add", nil)
            if safari.state == .runningForeground {
                notes.append("pressed home")
                XCUIDevice.shared.press(.home)
                sleep(2)
            }
            if icon.exists && !icon.isHittable {
                notes.append("swiped to next page")
                springboard.swipeLeft()
                sleep(1)
            }
            onHome = icon.exists && icon.isHittable
        }
        sleep(2)
        snap("home", springboard)
        mark("end")
        notes.append("icon on home: \(onHome)")
        save(clip, onHome ? "ok" : "failed", notes.joined(separator: "; "))
    }
}
