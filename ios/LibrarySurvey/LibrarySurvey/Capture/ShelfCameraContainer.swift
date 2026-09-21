import ARKit
import RealityKit
import SwiftUI

struct ShelfCameraContainer: UIViewRepresentable {
  @ObservedObject var store: ShelfCaptureStore
  @ObservedObject var camera: CameraSessionCoordinator

  func makeCoordinator() -> Coordinator {
    Coordinator(store: store, camera: camera)
  }

  func makeUIView(context: Context) -> ARView {
    let view = ARView(frame: .zero)
    view.automaticallyConfiguredSession = false
    context.coordinator.view = view
    context.coordinator.startIfAllowed()
    return view
  }

  func updateUIView(_ uiView: ARView, context: Context) {
    context.coordinator.view = uiView
    context.coordinator.startIfAllowed()
  }

  static func dismantleUIView(_ uiView: ARView, coordinator: Coordinator) {
    uiView.session.pause()
    Task { @MainActor in
      coordinator.stop()
    }
  }

  @MainActor
  final class Coordinator {
    let store: ShelfCaptureStore
    let camera: CameraSessionCoordinator
    weak var view: ARView?
    private var started = false

    init(store: ShelfCaptureStore, camera: CameraSessionCoordinator) {
      self.store = store
      self.camera = camera
    }

    func startIfAllowed() {
      guard !started, let view else { return }
      guard camera.tryAcquire(.shelfAR) else { return }
      let configuration = ARWorldTrackingConfiguration()
      configuration.planeDetection = [.vertical]
      view.session.run(configuration)
      store.attach(session: view.session)
      started = true
    }

    func stop() {
      view?.session.pause()
      store.detachSession()
      camera.release(.shelfAR)
      started = false
    }
  }
}
