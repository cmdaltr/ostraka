// Ostraka.app: a native window onto Ostraka. It either connects to a team's Ostraka server or
// runs the Docker stack on this Mac through the host `ostraka` command (desktop/README.md, the
// contract). This file holds the window, the start page, menus and the web view's behaviour;
// Local.swift holds running Ostraka here (Docker, the unpacked source tree, the first admin).
import AppKit
import WebKit

/// Where Ostraka runs for this window
enum Mode: Equatable {
    case server(URL)
    case local
}

enum Page { case start, ui }

let savedKey = "OstrakaServer"   // the user's choice: a server URL, or "local"
let managedKey = "ServerURL"     // set by IT through an MDM profile; locks the choice

/// A server address as the URL to open: https assumed when no scheme is given, and nothing kept
/// after the host and port, since Ostraka is served from the root. Plain http is only for this
/// computer, unless IT or OSTRAKA_SERVER set the address (`trusted`).
func parseServerAddress(_ text: String, trusted: Bool = false) -> URL? {
    var text = text.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !text.isEmpty else { return nil }
    if !text.contains("://") { text = "https://\(text)" }
    guard let parts = URLComponents(string: text), let host = parts.host, !host.isEmpty,
          let scheme = parts.scheme?.lowercased() else { return nil }
    let thisComputer = ["localhost", "127.0.0.1", "::1"].contains(host)
    guard scheme == "https" || (scheme == "http" && (thisComputer || trusted)) else { return nil }
    var clean = URLComponents()
    clean.scheme = scheme
    clean.host = host
    clean.port = parts.port
    return clean.url
}

/// Same scheme, host and port, with default ports filled in
func sameOrigin(_ a: URL, _ b: URL) -> Bool {
    func port(_ u: URL) -> Int? { u.port ?? (u.scheme == "https" ? 443 : u.scheme == "http" ? 80 : nil) }
    return a.scheme?.lowercased() == b.scheme?.lowercased()
        && a.host?.lowercased() == b.host?.lowercased() && port(a) == port(b)
}

@main
final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate,
                         WKDownloadDelegate, WKScriptMessageHandler, NSMenuItemValidation {
    var window: NSWindow!
    var webView: WKWebView!
    var page = Page.start
    var pageReady = false
    var pageQueue: [String] = []
    var licenceWindow: NSWindow?

    /// What this window is showing, once chosen
    var mode: Mode?
    /// IT set the server: no first-run screen and no Change Server
    var locked = false
    /// The local stack's address, from `ostraka url` (OSTRAKA_PORT can move it)
    var localURL = URL(string: "http://127.0.0.1:24747")!
    /// The local copy of Ostraka in use (Local.swift)
    var root: URL?

    static func main() {
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        app.setActivationPolicy(.regular)
        app.run()
    }

    var appVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
    }

    /// Where the web UI is for the current mode
    var baseURL: URL? {
        switch mode {
        case .server(let url)?: return url
        case .local?: return localURL
        case nil: return nil
        }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        let choice = configuredMode()
        locked = choice.locked
        buildMenu()

        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "ostraka")
        config.applicationNameForUserAgent = "Ostraka/\(appVersion)"
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = true

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1360, height: 860),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Ostraka"
        window.contentView = webView
        window.minSize = NSSize(width: 720, height: 520)
        window.center()
        window.setFrameAutosaveName("OstrakaMain")
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        switch choice.mode {
        case let mode?: start(mode)
        case nil where locked:
            showStart()
            message("Ostraka can't start", "Your organisation set Ostraka's server address, but it isn't one Ostraka can use. Ask your IT team to check the ServerURL setting.", error: true)
        case nil: showChoose()
        }
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows: Bool) -> Bool {
        if !hasVisibleWindows { window.makeKeyAndOrderFront(nil) }
        return true
    }

    // MARK: Which Ostraka

    /// IT's MDM setting first (locked), then OSTRAKA_SERVER, then the user's saved choice
    func configuredMode() -> (mode: Mode?, locked: Bool) {
        let defaults = UserDefaults.standard
        // objectIsForced is true only for a managed (MDM) value, never for one the user wrote
        if defaults.objectIsForced(forKey: managedKey) {
            let value = defaults.string(forKey: managedKey) ?? ""
            return (parseServerAddress(value, trusted: true).map(Mode.server), true)
        }
        if let env = ProcessInfo.processInfo.environment["OSTRAKA_SERVER"], !env.isEmpty {
            if env.lowercased() == "local" { return (.local, false) }
            if let url = parseServerAddress(env, trusted: true) { return (.server(url), false) }
        }
        if let saved = defaults.string(forKey: savedKey) {
            if saved == "local" { return (.local, false) }
            if let url = parseServerAddress(saved) { return (.server(url), false) }
        }
        return (nil, false)
    }

    func start(_ newMode: Mode) {
        mode = newMode
        switch newMode {
        case .server(let url): connect(to: url, fromChoose: false)
        case .local: runLocal()
        }
    }

    /// Checks `server` is an Ostraka (GET /api/meta answers "name": "Ostraka") over a connection
    /// the OS trusts, then opens it. URLSession's default handling is the system's trust store,
    /// so a certificate the Mac doesn't trust fails here before anything is shown.
    func connect(to server: URL, fromChoose: Bool) {
        let host = server.host ?? server.absoluteString
        if !fromChoose {
            showStart()
            progress("Connecting to \(host)…", nil, nil)
        }
        checkServer(server) { problem in
            if let problem = problem {
                if fromChoose {
                    self.js("choose", [self.chooseState(address: server.absoluteString, error: problem)])
                } else {
                    self.message("Can't open \(host)", problem, error: true, actions: self.retryActions())
                }
                return
            }
            if fromChoose { UserDefaults.standard.set(server.absoluteString, forKey: savedKey) }
            self.mode = .server(server)
            self.showUI(server)
        }
    }

    func checkServer(_ server: URL, _ done: @escaping (String?) -> Void) {
        let host = server.host ?? server.absoluteString
        var request = URLRequest(url: server.appendingPathComponent("api/meta"),
                                 cachePolicy: .reloadIgnoringLocalCacheData, timeoutInterval: 15)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        URLSession.shared.dataTask(with: request) { data, response, error in
            var problem: String?
            if let error = error {
                problem = self.connectionProblem(host, error)
            } else if let http = response as? HTTPURLResponse, http.statusCode != 200 {
                problem = "\(host) answered HTTP \(http.statusCode). Check the address: it should be the Ostraka server itself."
            } else {
                let meta = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
                if meta?["name"] as? String != "Ostraka" {
                    problem = "\(host) answered, but it isn't an Ostraka server. Check the address."
                }
            }
            DispatchQueue.main.async { done(problem) }
        }.resume()
    }

    /// What went wrong reaching a server, in terms of what to do about it
    func connectionProblem(_ host: String, _ error: Error) -> String {
        let code = (error as NSError).code
        let certificate = [NSURLErrorServerCertificateUntrusted, NSURLErrorServerCertificateHasUnknownRoot,
                           NSURLErrorServerCertificateHasBadDate, NSURLErrorServerCertificateNotYetValid,
                           NSURLErrorClientCertificateRejected, NSURLErrorSecureConnectionFailed]
        if certificate.contains(code) {
            return "This Mac doesn't trust \(host)'s certificate. If your team uses its own certificate authority, ask for its root certificate, add it in Keychain Access and set it to Always Trust, then try again."
        }
        if code == NSURLErrorAppTransportSecurityRequiresSecureConnection {
            return "\(host) needs to be reached over HTTPS."
        }
        return "Can't reach \(host) (\(error.localizedDescription)). Check the address and your network or VPN."
    }

    // MARK: The start page and the UI

    func show(_ newPage: Page) {
        page = newPage
        pageReady = false
        pageQueue = []
    }

    func showStart() {
        guard page != .start || webView.url?.isFileURL != true else { return }
        show(.start)
        window.title = "Ostraka"
        let start = Bundle.main.url(forResource: "start", withExtension: "html")!
        webView.loadFileURL(start, allowingReadAccessTo: Bundle.main.resourceURL!)
    }

    /// Calls a function on the start page, holding calls until the page has loaded
    func js(_ function: String, _ args: [Any]) {
        guard page == .start, let data = try? JSONSerialization.data(withJSONObject: args),
              let json = String(data: data, encoding: .utf8) else { return }
        let call = "\(function)(...\(json))"
        if pageReady { webView.evaluateJavaScript(call) } else { pageQueue.append(call) }
    }

    func progress(_ title: String, _ message: String?, _ pct: Int?) {
        js("progress", [title, message ?? "", pct.map { $0 as Any } ?? NSNull()])
    }

    func message(_ title: String, _ text: String, error: Bool = false, note: String? = nil,
                 actions: [[String: Any]] = []) {
        js("message", [["title": title, "text": text, "error": error, "note": note ?? "", "actions": actions]])
    }

    /// Try again, and a way out to the other mode unless IT fixed it
    func retryActions() -> [[String: Any]] {
        var actions: [[String: Any]] = [["label": "Try Again", "action": "retry", "primary": true]]
        if !locked { actions.append(["label": "Change Server…", "action": "change"]) }
        return actions
    }

    func chooseState(address: String = "", error: String? = nil, busy: Bool = false) -> [String: Any] {
        ["address": address, "error": error ?? NSNull(), "busy": busy,
         "home": (installTarget().path as NSString).abbreviatingWithTildeInPath]
    }

    func showChoose(error: String? = nil) {
        showStart()
        var address = ""
        if case .server(let url)? = mode { address = url.host ?? "" }
        js("choose", [chooseState(address: address, error: error)])
    }

    func showUI(_ url: URL) {
        show(.ui)
        if case .server(let server)? = mode {
            window.title = "Ostraka — \(server.host ?? "")"
        } else {
            window.title = "Ostraka"
        }
        webView.load(URLRequest(url: url))
    }

    /// Messages come from the start page only: the app's own file, in the main frame. A server's
    /// page, or a frame inside one, can't drive the app (start the stack, create the first admin).
    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) {
        guard page == .start, message.frameInfo.isMainFrame,
              message.frameInfo.securityOrigin.protocol == "file",
              let body = message.body as? [String: Any] else { return }
        switch body["action"] as? String {
        case "connect":
            let text = body["address"] as? String ?? ""
            guard let url = parseServerAddress(text) else {
                js("choose", [chooseState(address: text, error: "Enter the server's address, such as ostraka.example.org. Ostraka connects to a server over HTTPS only.")])
                return
            }
            js("choose", [chooseState(address: text, busy: true)])
            connect(to: url, fromChoose: true)
        case "local":
            UserDefaults.standard.set("local", forKey: savedKey)
            start(.local)
        case "retry":
            if let mode = mode { start(mode) } else { showChoose() }
        case "change":
            if !locked { showChoose() }
        case "admin":
            createFirstAdmin(body["email"] as? String ?? "")
        case "skipAdmin":
            firstAdminDone()
            showUI(localURL)
        case "start":
            start(.local)
        default:
            break
        }
    }

    // MARK: Menus

    @objc func showAbout(_ sender: Any?) {
        let credits = NSMutableAttributedString(
            string: "Incident management and response for teams.\n\nElastic License 2.0. The Pro and Services editions add the Ostraka Commercial Licence: Help › Licence.",
            attributes: [.font: NSFont.systemFont(ofSize: 11), .foregroundColor: NSColor.secondaryLabelColor])
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = .center
        credits.addAttribute(.paragraphStyle, value: paragraph, range: NSRange(location: 0, length: credits.length))
        NSApp.orderFrontStandardAboutPanel(options: [.applicationName: "Ostraka", .credits: credits])
        NSApp.activate(ignoringOtherApps: true)
    }

    /// The server's Help › Licence page, which is public. Before there's a server to ask (the
    /// first-run screen, a stack that's starting) the licence files this app carries instead.
    @objc func showLicence(_ sender: Any?) {
        if page == .ui, let base = baseURL {
            webView.load(URLRequest(url: base.appendingPathComponent("help/licence")))
        } else {
            showBundledLicence()
        }
    }

    func showBundledLicence() {
        if let shown = licenceWindow {
            shown.makeKeyAndOrderFront(nil)
            return
        }
        let read = { (name: String) in
            Bundle.main.url(forResource: name, withExtension: nil).flatMap { try? String(contentsOf: $0, encoding: .utf8) }
        }
        var text = read("LICENSE") ?? "The licence file is missing from this copy of the app. Ostraka is under the Elastic License 2.0."
        if let commercial = read("COMMERCIAL-LICENSE.md") { text += "\n\n\n" + commercial }

        let licence = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 680, height: 620),
                               styleMask: [.titled, .closable, .resizable, .miniaturizable],
                               backing: .buffered, defer: false)
        licence.title = "Ostraka Licence"
        licence.isReleasedWhenClosed = false
        let scroll = NSTextView.scrollableTextView()
        if let body = scroll.documentView as? NSTextView {
            body.isEditable = false
            body.font = NSFont.monospacedSystemFont(ofSize: 11, weight: .regular)
            body.string = text
            body.textContainerInset = NSSize(width: 10, height: 10)
        }
        licence.contentView = scroll
        licence.center()
        licence.makeKeyAndOrderFront(nil)
        licenceWindow = licence
    }

    @objc func changeServer(_ sender: Any?) {
        guard !locked else { return }
        showChoose()
    }

    @objc func stopOstraka(_ sender: Any?) {
        stopThen {
            self.message("Ostraka is stopped", "Your data is kept. Start it again when you need it.",
                         actions: [["label": "Start Ostraka", "action": "start", "primary": true]])
        }
    }

    @objc func stopAndQuit(_ sender: Any?) {
        stopThen { NSApp.terminate(nil) }
    }

    @objc func reload(_ sender: Any?) {
        if page == .ui { webView.reload() } else if let mode = mode { start(mode) }
    }

    @objc func openInBrowser(_ sender: Any?) {
        if let url = page == .ui ? webView.url : baseURL { NSWorkspace.shared.open(url) }
    }

    func validateMenuItem(_ menuItem: NSMenuItem) -> Bool {
        switch menuItem.action {
        case #selector(stopOstraka(_:)), #selector(stopAndQuit(_:)):
            // The stack on this Mac only: a server's admins start and stop theirs
            return mode == .local && root != nil
        case #selector(openInBrowser(_:)):
            return baseURL != nil
        case #selector(changeServer(_:)):
            return !locked
        default:
            return true
        }
    }

    func buildMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem()
        let appMenu = NSMenu(title: "Ostraka")
        appMenu.addItem(withTitle: "About Ostraka", action: #selector(showAbout(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        if !locked {
            appMenu.addItem(withTitle: "Change Server…", action: #selector(changeServer(_:)), keyEquivalent: ",")
        }
        appMenu.addItem(withTitle: "Stop Ostraka", action: #selector(stopOstraka(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide Ostraka", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let others = appMenu.addItem(withTitle: "Hide Others", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        others.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(withTitle: "Show All", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        let stopQuit = appMenu.addItem(withTitle: "Stop Ostraka and Quit", action: #selector(stopAndQuit(_:)), keyEquivalent: "q")
        stopQuit.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(withTitle: "Quit Ostraka", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        main.addItem(appItem)

        // Without an Edit menu carrying these selectors, ⌘C and ⌘V never reach the web view
        let editItem = NSMenuItem()
        let editMenu = NSMenu(title: "Edit")
        editMenu.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        editMenu.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        editMenu.addItem(.separator())
        editMenu.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        let plain = editMenu.addItem(withTitle: "Paste and Match Style", action: #selector(NSTextView.pasteAsPlainText(_:)), keyEquivalent: "V")
        plain.keyEquivalentModifierMask = [.command, .option, .shift]
        editMenu.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu
        main.addItem(editItem)

        let viewItem = NSMenuItem()
        let viewMenu = NSMenu(title: "View")
        viewMenu.addItem(withTitle: "Reload", action: #selector(reload(_:)), keyEquivalent: "r")
        viewMenu.addItem(withTitle: "Back", action: #selector(WKWebView.goBack(_:)), keyEquivalent: "[")
        viewMenu.addItem(withTitle: "Forward", action: #selector(WKWebView.goForward(_:)), keyEquivalent: "]")
        viewMenu.addItem(.separator())
        viewMenu.addItem(withTitle: "Open in Browser", action: #selector(openInBrowser(_:)), keyEquivalent: "o")
        viewMenu.addItem(.separator())
        let fullScreen = viewMenu.addItem(withTitle: "Enter Full Screen", action: #selector(NSWindow.toggleFullScreen(_:)), keyEquivalent: "f")
        fullScreen.keyEquivalentModifierMask = [.command, .control]
        viewItem.submenu = viewMenu
        main.addItem(viewItem)

        let windowItem = NSMenuItem()
        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Minimise", action: #selector(NSWindow.miniaturize(_:)), keyEquivalent: "m")
        windowMenu.addItem(withTitle: "Zoom", action: #selector(NSWindow.zoom(_:)), keyEquivalent: "")
        windowMenu.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        windowItem.submenu = windowMenu
        main.addItem(windowItem)
        NSApp.windowsMenu = windowMenu

        let helpItem = NSMenuItem()
        let helpMenu = NSMenu(title: "Help")
        helpMenu.addItem(withTitle: "Licence", action: #selector(showLicence(_:)), keyEquivalent: "")
        helpMenu.addItem(withTitle: "About Ostraka", action: #selector(showAbout(_:)), keyEquivalent: "")
        helpItem.submenu = helpMenu
        main.addItem(helpItem)
        NSApp.helpMenu = helpMenu

        NSApp.mainMenu = main
    }

    // MARK: Navigation, downloads, pop-ups

    /// Whether a URL belongs in the window: Ostraka's own origin, the start page, and the
    /// in-page schemes (blob: for report exports, about: for frames)
    func staysInWindow(_ url: URL) -> Bool {
        if url.isFileURL || ["about", "blob", "data"].contains(url.scheme ?? "") { return true }
        guard let base = baseURL else { return false }
        return sameOrigin(url, base)
    }

    /// Opens a URL outside the app, only for a link the user clicked and only for the web or
    /// mail. Anything else (smb:, vnc:, another app's URL scheme) from a script or a redirect
    /// would launch that app without a click, so it's refused.
    @discardableResult
    func openOutside(_ url: URL, _ action: WKNavigationAction) -> Bool {
        guard action.navigationType == .linkActivated,
              ["https", "http", "mailto"].contains(url.scheme?.lowercased() ?? "") else { return false }
        NSWorkspace.shared.open(url)
        return true
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if action.shouldPerformDownload {
            decisionHandler(.download)
            return
        }
        guard let url = action.request.url, !staysInWindow(url) else {
            decisionHandler(.allow)
            return
        }
        // Links elsewhere (ATT&CK, a vendor's advisory, Docker's download page) and mailto:
        // go to the default browser or mail app rather than taking over the window
        if action.targetFrame?.isMainFrame ?? true, openOutside(url, action) {
            decisionHandler(.cancel)
            return
        }
        // Another web origin may load in the window (a frame, a redirect); no other scheme may
        let web = ["http", "https"].contains(url.scheme?.lowercased() ?? "")
        decisionHandler(web ? .allow : .cancel)
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let disposition = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition") ?? ""
        decisionHandler(!response.canShowMIMEType || disposition.lowercased().hasPrefix("attachment") ? .download : .allow)
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    /// Reports (PDF and Word) and other exports go to ~/Downloads, never over an existing file
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let downloads = FileManager.default.urls(for: .downloadsDirectory, in: .userDomainMask)[0]
        let name = (suggestedFilename as NSString).lastPathComponent
        let base = (name as NSString).deletingPathExtension
        let ext = (name as NSString).pathExtension
        var target = downloads.appendingPathComponent(name)
        var n = 1
        while FileManager.default.fileExists(atPath: target.path) {
            target = downloads.appendingPathComponent(ext.isEmpty ? "\(base) (\(n))" : "\(base) (\(n)).\(ext)")
            n += 1
        }
        completionHandler(target)
    }

    func downloadDidFinish(_ download: WKDownload) {
        NSApp.requestUserAttention(.informationalRequest)
    }

    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        // target="_blank" and window.open: Ostraka's own pages stay in the window, a blob (a
        // report opened rather than saved) is downloaded, and anything else opens in the browser
        if let url = action.request.url {
            if url.scheme == "blob" || url.scheme == "data" {
                webView.startDownload(using: action.request) { $0.delegate = self }
            } else if staysInWindow(url) {
                webView.load(action.request)
            } else {
                openOutside(url, action)
            }
        }
        return nil
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.beginSheetModal(for: window) { _ in completionHandler() }
    }

    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        let alert = NSAlert()
        alert.messageText = message
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn) }
    }

    func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String,
                 defaultText: String?, initiatedByFrame frame: WKFrameInfo,
                 completionHandler: @escaping (String?) -> Void) {
        let alert = NSAlert()
        alert.messageText = prompt
        let field = NSTextField(frame: NSRect(x: 0, y: 0, width: 300, height: 24))
        field.stringValue = defaultText ?? ""
        alert.accessoryView = field
        alert.addButton(withTitle: "OK")
        alert.addButton(withTitle: "Cancel")
        alert.beginSheetModal(for: window) { completionHandler($0 == .alertFirstButtonReturn ? field.stringValue : nil) }
    }

    /// File inputs: brand logos, imports, attachments
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = parameters.allowsDirectories
        panel.beginSheetModal(for: window) { completionHandler($0 == .OK ? panel.urls : nil) }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        guard page == .start else { return }
        pageReady = true
        pageQueue.forEach { webView.evaluateJavaScript($0) }
        pageQueue = []
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        lostUI(error)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        lostUI(error)
    }

    /// The server or the stack went away under the window (stopped elsewhere, Docker quit, the
    /// network dropped): back to the start page with a way to try again
    func lostUI(_ error: Error) {
        let nsError = error as NSError
        guard page == .ui, nsError.domain == NSURLErrorDomain, nsError.code != NSURLErrorCancelled else { return }
        showStart()
        switch mode {
        case .server(let url)?:
            message("Lost the connection", connectionProblem(url.host ?? url.absoluteString, error),
                    error: true, actions: retryActions())
        default:
            message("Ostraka isn't answering", "Nothing answered on \(localURL.absoluteString) (\(error.localizedDescription)). Is Docker running?",
                    error: true, actions: retryActions())
        }
    }
}
