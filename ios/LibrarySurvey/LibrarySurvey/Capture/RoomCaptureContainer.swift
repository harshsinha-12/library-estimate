import RoomPlan
import SwiftUI

struct RoomCaptureContainer: UIViewRepresentable {
  @ObservedObject var store: RoomCaptureStore

  func makeCoordinator() -> Coordinator {
    Coordinator(store: store)
  }

  func makeUIView(context: Context) -> RoomCaptureView {
    let view = RoomCaptureView(frame: .zero)
    view.delegate = context.coordinator
    store.attach(session: view.captureSession)
    return view
  }

  func updateUIView(_ uiView: RoomCaptureView, context: Context) {}

  @objc(LibrarySurveyRoomCaptureCoordinator)
  final class Coordinator: NSObject, RoomCaptureViewDelegate {
    private let store: RoomCaptureStore

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
