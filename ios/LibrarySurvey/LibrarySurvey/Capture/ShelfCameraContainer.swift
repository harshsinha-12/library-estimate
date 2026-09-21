import ARKit
import RealityKit
import SwiftUI

struct ShelfCameraContainer: UIViewRepresentable {
  @ObservedObject var store: ShelfCaptureStore

  func makeUIView(context: Context) -> ARView {
    let view = ARView(frame: .zero)
    let configuration = ARWorldTrackingConfiguration()
    configuration.planeDetection = [.vertical]
    view.session.run(configuration)
    store.attach(session: view.session)
    return view
  }

  func updateUIView(_ uiView: ARView, context: Context) {}
}
