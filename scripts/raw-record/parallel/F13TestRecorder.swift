import Darwin
import Foundation

// Opt-in hardware verification. No other usage, text, device identifier, or
// original input report is written to this narrowly scoped test log.
@MainActor
final class F13TestRecorder {
  static let shared = F13TestRecorder()

  private let enabled = CommandLine.arguments.contains("--record-f13")

  func record(down: Bool) {
    guard enabled else { return }
    let base = ProcessInfo.processInfo.environment["XDG_DATA_HOME"].map {
      URL(fileURLWithPath: $0)
    } ?? FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".local/share")
    let directory = base.appendingPathComponent("karabiner-raw-record-mvp")
    do {
      try FileManager.default.createDirectory(
        at: directory, withIntermediateDirectories: true,
        attributes: [.posixPermissions: 0o700])
      let path = directory.appendingPathComponent("f13-test.jsonl").path
      let descriptor = Darwin.open(
        path, O_WRONLY | O_APPEND | O_CREAT | O_NOFOLLOW, mode_t(0o600))
      guard descriptor >= 0 else { return }
      defer { Darwin.close(descriptor) }
      var data = try JSONSerialization.data(withJSONObject: [
        "type": down ? "down" : "up",
        "time": Date().timeIntervalSince1970,
      ])
      data.append(0x0a)
      _ = data.withUnsafeBytes { bytes in
        Darwin.write(descriptor, bytes.baseAddress, bytes.count)
      }
    } catch {
      // Recording failure must never affect event forwarding or capture.
    }
  }
}
