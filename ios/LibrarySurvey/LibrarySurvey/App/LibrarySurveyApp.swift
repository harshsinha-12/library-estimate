import SwiftUI

@main
struct LibrarySurveyApp: App {
  var body: some Scene {
    WindowGroup {
      RootView()
        .onAppear {
          LocalNetworkPrompter.shared.promptIfNeeded()
        }
    }
  }
}

