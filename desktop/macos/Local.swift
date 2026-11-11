// Running Ostraka on this Mac: the source tree (the copy a release build carries, unpacked into
// ~/Ostraka, or the checkout a local build was made from), Docker, `ostraka start --progress`,
// and the first admin on a fresh install. Everything here goes through the host `ostraka`
// command as desktop/README.md specifies it.
import AppKit

let versionMarker = ".ostraka-version"
let adminDoneKey = "OstrakaFirstAdminDone"  // the copies whose first admin is settled
/// `ostraka first-admin` exits 5 when there's an admin already; any other failure settles nothing
let exitHasAdmin: Int32 = 5
/// What a mirroring update keeps in the copy: the settings and the version marker
let keptOnUpdate: Set<String> = [".env", versionMarker]

/// A folder Ostraka can run from: the host command and the compose file
func isOstrakaCopy(_ url: URL) -> Bool {
    FileManager.default.isExecutableFile(atPath: url.appendingPathComponent("ostraka").path)
        && FileManager.default.fileExists(atPath: url.appendingPathComponent("docker-compose.yml").path)
}

/// A copy the app unpacked (and may update), as opposed to a git checkout, which is never overwritten
func isAppCopy(_ url: URL) -> Bool {
    FileManager.default.fileExists(atPath: url.appendingPathComponent(versionMarker).path)
        && !FileManager.default.fileExists(atPath: url.appendingPathComponent(".git").path)
}

/// Whether version `a` is later than `b`, comparing the numbers in each part (0.10.0 > 0.9.2)
func isNewer(_ a: String, than b: String) -> Bool {
    a.compare(b, options: .numeric) == .orderedDescending
}

/// The environment for child processes. An app opened from the Dock or Finder gets
/// /usr/bin:/bin:/usr/sbin:/sbin and nothing else, which misses Docker's CLI wherever
/// OrbStack, Docker Desktop or Homebrew put it.
func processEnvironment(home: URL? = nil) -> [String: String] {
    var env = ProcessInfo.processInfo.environment
    let user = NSHomeDirectory()
    let extra = ["\(user)/.orbstack/bin", "/Applications/Docker.app/Contents/Resources/bin",
                 "\(user)/.docker/bin", "/opt/homebrew/bin", "/usr/local/bin"]
    env["PATH"] = (extra + [env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"]).joined(separator: ":")
    if let home = home { env["OSTRAKA_HOME"] = home.path }
    return env
}

/// Runs a command to completion (call off the main thread); returns its status and output
@discardableResult
func run(_ executable: String, _ args: [String], cwd: URL? = nil, env: [String: String]? = nil) -> (status: Int32, output: String) {
    let process = Process()
    process.executableURL = URL(fileURLWithPath: executable)
    process.arguments = args
    process.environment = env ?? processEnvironment()
    if let cwd = cwd { process.currentDirectoryURL = cwd }
    let pipe = Pipe()
    process.standardOutput = pipe
    process.standardError = pipe
    do {
        try process.run()
    } catch {
        return (-1, error.localizedDescription)
    }
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    process.waitUntilExit()
    return (process.terminationStatus, String(data: data, encoding: .utf8) ?? "")
}

struct LocalError: LocalizedError {
    let title: String
    let message: String
    var note: String? = nil
    /// Buttons for the start page besides Try Again, e.g. links to Docker's download pages
    var actions: [[String: Any]] = []
    var errorDescription: String? { message }
}

enum Docker {
    case running(String)
    case stopped(String, app: String?)
    case missing
}

extension AppDelegate {
    /// The source tree a release build carries (package.sh puts it in Resources)
    var bundledSource: URL? {
        Bundle.main.url(forResource: "ostraka-src", withExtension: "tar.gz")
    }

    /// The checkout a local build was made from (build.sh records it in Info.plist)
    var checkoutRoot: URL? {
        (Bundle.main.object(forInfoDictionaryKey: "OstrakaRoot") as? String).map { URL(fileURLWithPath: $0) }
    }

    /// Where Ostraka's files are, or go: OSTRAKA_HOME, then a local build's checkout, then ~/Ostraka
    func installTarget() -> URL {
        if let home = ProcessInfo.processInfo.environment["OSTRAKA_HOME"], !home.isEmpty {
            return URL(fileURLWithPath: (home as NSString).expandingTildeInPath)
        }
        if let checkout = checkoutRoot, bundledSource == nil { return checkout }
        return URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Ostraka")
    }

    // MARK: Starting

    func runLocal() {
        showStart()
        js("clearLog", [])
        progress("Starting Ostraka…", "Checking Ostraka's files", nil)
        let target = installTarget()

        if isOstrakaCopy(target), isAppCopy(target), bundledSource != nil,
           let installed = try? String(contentsOf: target.appendingPathComponent(versionMarker), encoding: .utf8)
                .trimmingCharacters(in: .whitespacesAndNewlines),
           isNewer(appVersion, than: installed) {
            askToUpdate(target, from: installed)
            return
        }
        prepare(target, unpack: !isOstrakaCopy(target))
    }

    /// Unpacks if asked, then checks Docker, then starts the stack. Off the main thread.
    func prepare(_ target: URL, unpack: Bool) {
        DispatchQueue.global().async {
            do {
                if unpack {
                    self.onMain { self.progress("Starting Ostraka…", "Putting Ostraka's files in \(self.tilde(target))", nil) }
                    try self.unpackSource(into: target)
                }
                guard isOstrakaCopy(target) else {
                    throw LocalError(title: "Ostraka's files are missing",
                                     message: "There's no Ostraka in \(self.tilde(target)): it needs the ostraka command and docker-compose.yml.")
                }
                let url = run("/bin/bash", [target.appendingPathComponent("ostraka").path, "url"],
                              cwd: target, env: processEnvironment(home: target))
                let printed = url.output.split(whereSeparator: \.isNewline).last.map(String.init) ?? ""
                self.onMain {
                    self.root = target
                    if url.status == 0, let parsed = URL(string: printed.trimmingCharacters(in: .whitespaces)), parsed.host != nil {
                        self.localURL = parsed
                    }
                }
                try self.ensureDocker()
                self.onMain { self.startStack(target) }
            } catch let error as LocalError {
                self.onMain { self.localProblem(error) }
            } catch {
                self.onMain { self.localProblem(LocalError(title: "Ostraka can't start", message: error.localizedDescription)) }
            }
        }
    }

    func localProblem(_ error: LocalError) {
        message(error.title, error.message, error: true, note: error.note, actions: error.actions + retryActions())
    }

    func startStack(_ target: URL) {
        progress("Starting Ostraka…", "Starting the Docker stack", 0)
        runOstraka(["start", "--progress"], in: target, output: { line in
            // `progress <0-100> <message>` drives the bar; everything else is the log
            let parts = line.split(separator: " ", maxSplits: 2, omittingEmptySubsequences: false)
            if parts.count >= 2, parts[0] == "progress", let pct = Int(parts[1]) {
                self.progress("Starting Ostraka…", parts.count > 2 ? String(parts[2]) : "", max(0, min(100, pct)))
            } else {
                self.js("line", [line])
            }
        }) { status in
            guard status == 0 else {
                let text = status == 4
                    ? "Docker isn't running or isn't installed. Start OrbStack or Docker Desktop, then try again."
                    : "'ostraka start' stopped with status \(status). The details are below."
                self.message("Ostraka didn't start", text, error: true, actions: self.retryActions())
                return
            }
            self.stackIsUp(target)
        }
    }

    func stackIsUp(_ target: URL) {
        let done = UserDefaults.standard.stringArray(forKey: adminDoneKey) ?? []
        if done.contains(target.path) {
            showUI(localURL)
        } else {
            js("admin", [["busy": false]])
        }
    }

    // MARK: The first admin

    /// `ostraka first-admin EMAIL` prints only the invitation link, which the window opens:
    /// password and TOTP are set there, as on any server
    func createFirstAdmin(_ email: String) {
        let email = email.trimmingCharacters(in: .whitespacesAndNewlines)
        guard email.range(of: #"^[^@\s]+@[^@\s]+\.[^@\s]+$"#, options: .regularExpression) != nil else {
            js("admin", [["error": "Enter an email address, such as you@example.org."]])
            return
        }
        guard let target = root else { return }
        js("admin", [["busy": true]])
        var output: [String] = []
        runOstraka(["first-admin", email], in: target, output: { output.append($0) }) { status in
            let link = output.reversed().lazy
                .map { $0.trimmingCharacters(in: .whitespaces) }
                .first { $0.hasPrefix("http://") || $0.hasPrefix("https://") }
                .flatMap(URL.init(string:))
            if status == 0, let link = link {
                self.firstAdminDone()
                self.showUI(link)
            } else if status == exitHasAdmin {
                // Refused: there's an admin already, so this copy needs no first-run step again
                self.firstAdminDone()
                let said = output.joined(separator: " ").trimmingCharacters(in: .whitespaces)
                self.message("There's an admin already", said.isEmpty ? "This Ostraka has an admin, who can invite you." : said,
                             actions: [["label": "Open Ostraka", "action": "skipAdmin", "primary": true]])
            } else {
                // A database that isn't ready yet, say: nothing is settled, so the form stays and
                // comes back on the next start until it works
                let said = output.suffix(4).joined(separator: " ")
                self.js("admin", [["error": "Couldn't make the invitation: 'ostraka first-admin' stopped with status \(status). \(said) Try again, or quit: Ostraka asks again next time."]])
            }
        }
    }

    func firstAdminDone() {
        guard let target = root else { return }
        var done = UserDefaults.standard.stringArray(forKey: adminDoneKey) ?? []
        if !done.contains(target.path) { done.append(target.path) }
        UserDefaults.standard.set(done, forKey: adminDoneKey)
    }

    // MARK: Stopping

    func stopThen(_ next: @escaping () -> Void) {
        guard let target = root, mode == .local else {
            next()
            return
        }
        showStart()
        js("clearLog", [])
        progress("Stopping Ostraka…", "Your data is kept", nil)
        runOstraka(["stop"], in: target, output: { self.js("line", [$0]) }) { _ in next() }
    }

    // MARK: The host command

    /// Runs `ostraka <args>` from `target`, passing each line of output to `output` and the
    /// exit status to `completion`, both on the main thread
    func runOstraka(_ args: [String], in target: URL, output: @escaping (String) -> Void,
                    completion: @escaping (Int32) -> Void) {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/bash")
        process.arguments = [target.appendingPathComponent("ostraka").path] + args
        process.currentDirectoryURL = target
        process.environment = processEnvironment(home: target)

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        // Output arrives in arbitrary chunks: hold any partial line until its newline comes
        var pending = Data()
        let emit = { (data: Data, flush: Bool) in
            pending.append(data)
            var lines: [String] = []
            while let newline = pending.firstIndex(of: 0x0A) {
                lines.append(String(decoding: pending[pending.startIndex..<newline], as: UTF8.self))
                pending.removeSubrange(pending.startIndex...newline)
            }
            if flush, !pending.isEmpty {
                lines.append(String(decoding: pending, as: UTF8.self))
                pending.removeAll()
            }
            let clean = lines.map { $0.trimmingCharacters(in: CharacterSet(charactersIn: "\r")) }.filter { !$0.isEmpty }
            if !clean.isEmpty { DispatchQueue.main.async { clean.forEach(output) } }
        }
        let reader = DispatchQueue(label: "ostraka.output")
        pipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            reader.async { if !data.isEmpty { emit(data, false) } }
        }
        process.terminationHandler = { p in
            pipe.fileHandleForReading.readabilityHandler = nil
            let rest = pipe.fileHandleForReading.readDataToEndOfFile()
            reader.async {
                emit(rest, true)
                DispatchQueue.main.async { completion(p.terminationStatus) }
            }
        }
        do {
            try process.run()
        } catch {
            output("Couldn't run \(target.path)/ostraka: \(error.localizedDescription)")
            completion(-1)
        }
    }

    // MARK: Docker

    /// OrbStack or Docker Desktop, running. One that's installed but not running is opened and
    /// waited for; one that's missing is never installed for the user, only linked. Off the main thread.
    func ensureDocker() throws {
        switch dockerState() {
        case .running:
            return
        case .missing:
            throw LocalError(title: "Ostraka needs Docker",
                             message: "Running Ostraka on this Mac needs OrbStack or Docker Desktop, and neither is installed. Install one, open it once to finish its setup, then try again.",
                             note: "OrbStack and Docker Desktop are free for personal use; commercial use of either may need a paid licence.",
                             actions: [["label": "Get OrbStack", "href": "https://orbstack.dev/download"],
                                       ["label": "Get Docker Desktop", "href": "https://www.docker.com/products/docker-desktop/"]])
        case .stopped(let name, let app):
            guard let app = app else {
                throw LocalError(title: "Docker isn't running",
                                 message: "Docker is installed but isn't answering. Start it, then try again.")
            }
            onMain { self.progress("Starting Ostraka…", "Waiting for \(name) to start", nil) }
            run("/usr/bin/open", ["-a", app])
            let deadline = Date().addingTimeInterval(120)
            while Date() < deadline {
                Thread.sleep(forTimeInterval: 3)
                if case .running = dockerState() { return }
            }
            throw LocalError(title: "\(name) isn't ready",
                             message: "\(name) is installed but Docker didn't answer within two minutes. Open \(name), finish anything it asks for, then try again.")
        }
    }

    func dockerState() -> Docker {
        let fm = FileManager.default
        let app = { (name: String) -> String? in
            ["/Applications", "\(NSHomeDirectory())/Applications"]
                .map { "\($0)/\(name).app" }.first { fm.fileExists(atPath: $0) }
        }
        let orb = app("OrbStack"), desktop = app("Docker")
        let name = orb != nil ? "OrbStack" : desktop != nil ? "Docker Desktop" : "Docker"
        if run("/usr/bin/env", ["docker", "info"]).status == 0 { return .running(name) }
        if let path = orb ?? desktop { return .stopped(name, app: path) }
        if run("/usr/bin/env", ["which", "docker"]).status == 0 { return .stopped(name, app: nil) }
        return .missing
    }

    // MARK: The bundled source tree

    /// Unpacks the source tree this build carries into `target`. An earlier copy this app made
    /// (it has .ostraka-version, no .git) is mirrored: everything but .env and the marker is
    /// removed before the new tree goes in, since a file the new version dropped (an Alembic
    /// revision) would otherwise stay. Only `target`'s own entries are removed, and a symlink
    /// among them as a link, so nothing outside it is touched. The data is in Docker volumes.
    func unpackSource(into target: URL) throws {
        let fm = FileManager.default
        guard let source = bundledSource else {
            throw LocalError(title: "Ostraka's files are missing",
                             message: "There's no Ostraka in \(tilde(target)), and this build of the app doesn't carry Ostraka's files. Build the app from a checkout with desktop/macos/build.sh, or use a release.")
        }
        if fm.fileExists(atPath: target.path), !isAppCopy(target) {
            let contents = (try? fm.contentsOfDirectory(atPath: target.path)) ?? []
            if !contents.filter({ $0 != ".DS_Store" }).isEmpty {
                throw LocalError(title: "\(tilde(target)) is in use",
                                 message: "\(tilde(target)) already exists and isn't a copy of Ostraka this app made, so it's left alone. Move it aside, or set OSTRAKA_HOME to another folder, then try again.")
            }
        }
        let work = fm.temporaryDirectory.appendingPathComponent("ostraka-unpack-\(UUID().uuidString)")
        try fm.createDirectory(at: work, withIntermediateDirectories: true)
        defer { try? fm.removeItem(at: work) }
        let unpack = run("/usr/bin/tar", ["-xzf", source.path, "-C", work.path])
        guard unpack.status == 0 else {
            throw LocalError(title: "Couldn't unpack Ostraka", message: unpack.output)
        }
        // The tarball may or may not wrap the tree in one top folder (git archive --prefix)
        var top = work
        let entries = try fm.contentsOfDirectory(atPath: work.path)
        if !entries.contains("docker-compose.yml"), entries.count == 1 {
            top = work.appendingPathComponent(entries[0])
        }
        guard isOstrakaCopy(top) else {
            throw LocalError(title: "Couldn't unpack Ostraka", message: "The files this app carries have no docker-compose.yml: reinstall Ostraka.")
        }
        try fm.createDirectory(at: target, withIntermediateDirectories: true)
        if isAppCopy(target) { try clearCopy(target) }
        let copy = run("/usr/bin/ditto", [top.path, target.path])
        guard copy.status == 0 else {
            throw LocalError(title: "Couldn't copy Ostraka into \(tilde(target))", message: copy.output)
        }
        try appVersion.write(to: target.appendingPathComponent(versionMarker), atomically: true, encoding: .utf8)
    }

    /// Empties a copy this app made, keeping `keptOnUpdate`. If what follows fails, the marker
    /// is still there, so the next start unpacks again rather than calling the folder foreign.
    func clearCopy(_ target: URL) throws {
        let fm = FileManager.default
        guard isAppCopy(target) else { return }
        let resolved = target.resolvingSymlinksInPath().standardizedFileURL
        guard resolved.path != "/", resolved.path != URL(fileURLWithPath: NSHomeDirectory()).standardizedFileURL.path else {
            throw LocalError(title: "Couldn't update Ostraka", message: "\(tilde(target)) isn't a folder Ostraka can update in place. Set OSTRAKA_HOME to a folder of its own.")
        }
        for name in try fm.contentsOfDirectory(atPath: resolved.path) where !keptOnUpdate.contains(name) {
            // removeItem on a symlink removes the link, never what it points at
            try fm.removeItem(at: resolved.appendingPathComponent(name))
        }
    }

    func askToUpdate(_ target: URL, from installed: String) {
        let alert = NSAlert()
        alert.messageText = "Update Ostraka to \(appVersion)?"
        alert.informativeText = "\(tilde(target)) has Ostraka \(installed). Updating replaces its program files; your settings (.env) and data are kept. The next start rebuilds the containers, which takes a few minutes."
        alert.addButton(withTitle: "Update")
        alert.addButton(withTitle: "Not Now")
        alert.beginSheetModal(for: window) { response in
            self.prepare(target, unpack: response == .alertFirstButtonReturn)
        }
    }

    // MARK: Helpers

    func onMain(_ block: @escaping () -> Void) { DispatchQueue.main.async(execute: block) }

    func tilde(_ url: URL) -> String { (url.path as NSString).abbreviatingWithTildeInPath }
}
