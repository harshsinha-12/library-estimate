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
              .accessibilityHidden(true)
            VStack(alignment: .leading) {
              Text(item.passed ? "Passed" : "Needs attention")
                .font(.caption.weight(.semibold))
              Text(item.title).font(.headline)
              Text(item.detail).font(.caption).foregroundStyle(.secondary)
            }
          }
          .accessibilityElement(children: .combine)
          .accessibilityLabel("\(item.title), \(item.passed ? "passed" : "needs attention"). \(item.detail)")
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
        .minimumScaledTouchTarget()

        Button("Start Room Pass", action: onContinue)
          .disabled(items.contains { $0.required && !$0.passed })
          .minimumScaledTouchTarget()
      }

      Section("Capture fallback") {
        Text(
          "RoomPlan owns the camera during the room scan. Shelf photos use a still camera only after that session stops. Apple does not allow a second AVCaptureSession or optical zoom during RoomPlan."
        )
        Text(
          "If sampled RGB frames cannot be collected during RoomPlan, the capture screen shows Sequential fallback as a status, keeps the processed room, and uses the final AR frame. After the room scan, photograph each shelf or attach photos."
        )
      }
    }
    .onAppear {
      LocalNetworkPrompter.shared.promptIfNeeded()
      refresh()
    }
    .accessibilityStatusAnnouncements(deviceStatus)
  }

  private var deviceStatus: String {
    if requesting { return "Requesting camera, microphone, and local network permissions" }
    let failed = items.filter { $0.required && !$0.passed }.count
    return failed == 0 ? "Device checks passed" : "\(failed) required device checks need attention"
  }

  private func refresh() {
    items = DeviceCheckService.snapshot(locationAuthorized: locationAvailable)
  }
}
