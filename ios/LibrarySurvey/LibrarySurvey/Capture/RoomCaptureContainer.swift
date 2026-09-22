import RoomPlan
import SwiftUI

struct RoomCaptureContainer: UIViewRepresentable {
  @ObservedObject var store: RoomCaptureStore
  @ObservedObject var camera: CameraSessionCoordinator

  func makeCoordinator() -> Coordinator {
    Coordinator(store: store)
  }

  func makeUIView(context: Context) -> RoomCaptureView {
    let view = RoomCaptureView(frame: .zero)
    view.delegate = context.coordinator
    view.isAccessibilityElement = true
    view.accessibilityLabel = "Live RoomPlan geometry capture"
    view.accessibilityHint = "This camera view captures room geometry. It does not provide optical zoom. Use the scan controls to pause or finish."
    store.attach(session: view.captureSession)
    return view
  }

  func updateUIView(_ uiView: RoomCaptureView, context: Context) {}

  static func dismantleUIView(_ uiView: RoomCaptureView, coordinator: Coordinator) {
    uiView.captureSession.arSession.pause()
    Task { @MainActor in
      coordinator.store.didDismantleCaptureView()
    }
  }

  @objc(LibrarySurveyRoomCaptureCoordinator)
  final class Coordinator: NSObject, RoomCaptureViewDelegate {
    let store: RoomCaptureStore

    init(store: RoomCaptureStore) {
      self.store = store
    }

    required init?(coder: NSCoder) {
      nil
    }

    func encode(with coder: NSCoder) {}

    func captureView(
      shouldPresent roomDataForProcessing: CapturedRoomData,
      error: Error?
    ) -> Bool {
      if let error {
        Task { @MainActor in store.didFinish(room: nil, error: error) }
        return false
      }
      return true
    }

    func captureView(didPresent processedResult: CapturedRoom, error: Error?) {
      Task { @MainActor in
        store.didFinish(room: error == nil ? processedResult : nil, error: error)
      }
    }
  }
}
