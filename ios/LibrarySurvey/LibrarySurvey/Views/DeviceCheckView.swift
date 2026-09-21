import SwiftUI

struct DeviceCheckView: View {
  let locationAvailable: Bool
  let onContinue: () -> Void
  @State private var items: [DeviceCheckItem] = []
  @State private var requesting = false

  var body: some View {
    List {
      Section {
        ForEach(items) { item in
          HStack(alignment: .top) {
            Image(systemName: item.passed ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
              .foregroundStyle(item.passed ? .green : item.required ? .red : .orange)
              .accessibilityLabel(item.passed ? "Passed" : "Needs attention")
            VStack(alignment: .leading) {
              Text(item.title).font(.headline)
              Text(item.detail).font(.caption).foregroundStyle(.secondary)
            }
          }
        }
      }

      Section {
        Button("Request Camera, Microphone, and Local Network", systemImage: "checkmark.shield") {
          requesting = true
          Task {
            LocalNetworkPrompter.shared.promptIfNeeded()
            await DeviceCheckService.requestMediaPermissions()
            refresh()
            requesting = false
          }
        }
        .disabled(requesting)

        Button("Start Room Pass", action: onContinue)
          .disabled(items.contains { $0.required && !$0.passed })
      }

      Section("Capture fallback") {
        Text(
          "If sampled RGB frames cannot be collected during RoomPlan, the app keeps the "
            + "processed room and captures the final AR frame as sequential evidence."
        )
      }
    }
    .onAppear {
      LocalNetworkPrompter.shared.promptIfNeeded()
      refresh()
    }
  }

  private func refresh() {
    items = DeviceCheckService.snapshot(locationAuthorized: locationAvailable)
  }
}
