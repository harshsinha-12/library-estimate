import SceneKit
import SwiftUI

struct SurveySpatialEvidenceView: View {
  let surveyId: UUID
  let backendURL: URL
  let layout: FloorPlanLayout
  let usdzURL: URL

  @State private var overview: Stage4Overview?
  @State private var selectedFaceId: String?
  @State private var message: String?

  var body: some View {
    List {
      Section("2D shelf selection") {
        GeometryReader { geometry in
          ZStack {
            TaggedFloorPlanCanvas(layout: layout)
            ForEach(layout.shelves) { shelf in
              let a = layout.screen(.init(x: shelf.minX, z: shelf.maxZ), in: geometry.size)
              let b = layout.screen(.init(x: shelf.maxX, z: shelf.minZ), in: geometry.size)
              Button { selectedFaceId = shelf.faceId } label: {
                Rectangle()
                  .fill(.clear)
                  .contentShape(Rectangle())
                  .frame(width: max(44, abs(a.x - b.x)), height: max(44, abs(a.y - b.y)))
              }
              .position(x: (a.x + b.x) / 2, y: (a.y + b.y) / 2)
              .accessibilityLabel("Select \(shelf.label), \(shelf.placementLabel)")
              .accessibilityValue(selectedFaceId == shelf.faceId ? "Selected" : "Not selected")
              .accessibilityHint("Shows copies and evidence for this shelf")
            }
          }
        }
        .frame(height: 280)
        .clipShape(RoundedRectangle(cornerRadius: 16))
      }
      Section("3D shelf selection") {
        RegisteredShelfScene(url: usdzURL, shelves: layout.shelves, selectedFaceId: $selectedFaceId)
          .frame(height: 300)
          .accessibilityLabel("Interactive RoomPlan 3D model")
          .accessibilityValue(selectedFaceId.map { "Selected shelf \($0)" } ?? "No shelf selected")
          .accessibilityHint("Use the shelf actions to select an operator-placed shelf. Unregistered shelves have no measured 3D target.")
          .accessibilityActions {
            ForEach(layout.shelves.filter { $0.placement == ShelfFootprint.operatorKind }) { shelf in
              Button("Select \(shelf.label)") { selectedFaceId = shelf.faceId }
            }
          }
        Text("Blue 3D volumes use operator-placed shelf footprints. They are approximate. Unregistered shelves have no 3D hit target.")
          .font(.footnote)
      }
      Section("Shelves") {
        ForEach(layout.shelves) { shelf in
          Button("\(shelf.label) · \(shelf.placementLabel)") {
            selectedFaceId = shelf.faceId
          }
          .minimumScaledTouchTarget()
          .accessibilityValue(selectedFaceId == shelf.faceId ? "Selected" : "Not selected")
        }
      }
      if let faceId = selectedFaceId {
        Section("\(faceId) copies and evidence") {
          let copies = overview?.copies.filter { $0.faceId == faceId } ?? []
          ForEach(copies) { copy in
            NavigationLink {
              PriceEvidenceView(surveyId: surveyId, copy: copy, backendURL: backendURL)
            } label: {
              Text("Slot \(copy.slot.map(String.init) ?? "?") · \(copy.title ?? copy.label ?? "Unidentified copy")")
            }
          }
          if copies.isEmpty { Text("No processed copies on this face yet.") }
        }
      }
      if let message { AccessibleStatusLabel(text: message, kind: .error) }
    }
    .navigationTitle("Spatial evidence")
    .task { await load() }
    .accessibilityStatusAnnouncements(accessibilityStatus)
  }

  private var accessibilityStatus: String {
    if let message { return "Spatial evidence error. \(message)" }
    if let selectedFaceId { return "Selected shelf \(selectedFaceId)" }
    return overview == nil ? "Loading spatial evidence" : "Spatial evidence loaded"
  }

  private func load() async {
    do {
      let url = backendURL.appendingPathComponent("v1/surveys/\(surveyId.uuidString)/overview")
      let (data, response) = try await OperatorSession.data(from: url)
      guard (response as? HTTPURLResponse)?.statusCode == 200 else {
        throw URLError(.badServerResponse)
      }
      overview = try JSONCoding.decoder().decode(Stage4Overview.self, from: data)
      message = nil
    } catch { message = error.localizedDescription }
  }
}

private struct RegisteredShelfScene: UIViewRepresentable {
  let url: URL
  let shelves: [FloorPlanLayout.ShelfOverlay]
  @Binding var selectedFaceId: String?

  func makeCoordinator() -> Coordinator {
    Coordinator(selectedFaceId: $selectedFaceId, faceIds: Set(shelves.map(\.faceId)))
  }

  func makeUIView(context: Context) -> SCNView {
    let view = SCNView()
    view.autoenablesDefaultLighting = true
    view.allowsCameraControl = true
    view.backgroundColor = UIColor(white: 0.16, alpha: 1)
    view.scene = try? SCNScene(url: url, options: [.checkConsistency: false])
    view.isAccessibilityElement = true
    view.accessibilityLabel = view.scene == nil
      ? "3D RoomPlan model unavailable"
      : "Interactive RoomPlan 3D model"
    view.accessibilityHint = "Direct 3D manipulation is visual. Use the adjacent shelf list or accessibility actions to select a shelf."
    addShelves(to: view.scene)
    let tap = UITapGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.tap(_:)))
    view.addGestureRecognizer(tap)
    return view
  }

  func updateUIView(_ view: SCNView, context: Context) {
    context.coordinator.selectedFaceId = $selectedFaceId
  }

  static func dismantleUIView(_ view: SCNView, coordinator: Coordinator) {
    view.scene = nil
    view.pause(nil)
  }

  private func addShelves(to scene: SCNScene?) {
    guard let scene else { return }
    for shelf in shelves where shelf.placement == ShelfFootprint.operatorKind {
      let width = max(0.08, abs(shelf.maxX - shelf.minX))
      let depth = max(0.08, abs(shelf.maxZ - shelf.minZ))
      let box = SCNBox(width: CGFloat(width), height: 1.8, length: CGFloat(depth), chamferRadius: 0.02)
      box.firstMaterial?.diffuse.contents = UIColor.systemBlue.withAlphaComponent(0.35)
      box.firstMaterial?.isDoubleSided = true
      let node = SCNNode(geometry: box)
      node.name = shelf.faceId
      node.position = SCNVector3(
        Float((shelf.minX + shelf.maxX) / 2), 0.9,
        Float((shelf.minZ + shelf.maxZ) / 2)
      )
      scene.rootNode.addChildNode(node)
    }
  }

  final class Coordinator: NSObject {
    var selectedFaceId: Binding<String?>
    let faceIds: Set<String>
    init(selectedFaceId: Binding<String?>, faceIds: Set<String>) {
      self.selectedFaceId = selectedFaceId
      self.faceIds = faceIds
    }

    @objc func tap(_ gesture: UITapGestureRecognizer) {
      guard let view = gesture.view as? SCNView else { return }
      for hit in view.hitTest(gesture.location(in: view), options: [:]) {
        if let name = hit.node.name, faceIds.contains(name) {
          selectedFaceId.wrappedValue = name
          return
        }
      }
    }
  }
}
