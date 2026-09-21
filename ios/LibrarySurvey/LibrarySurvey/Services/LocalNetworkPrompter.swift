import Foundation
import Network

/// iOS only shows the Local Network dialog when an app browses or connects on LAN.
/// Keep the browser alive so the prompt is not tied to Test Connection on the 3D screen.
@MainActor
final class LocalNetworkPrompter {
  static let shared = LocalNetworkPrompter()

  private var browser: NWBrowser?

  func promptIfNeeded() {
    guard browser == nil else { return }
    let browser = NWBrowser(
      for: .bonjour(type: "_http._tcp", domain: "local."),
      using: .tcp
    )
    browser.stateUpdateHandler = { _ in }
    browser.start(queue: .main)
    self.browser = browser
  }
}
