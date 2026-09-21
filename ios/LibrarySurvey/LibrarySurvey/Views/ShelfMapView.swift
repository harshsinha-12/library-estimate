import SwiftUI

struct ShelfMapView: View {
  @ObservedObject var store: ShelfCaptureStore
  @ObservedObject var camera: CameraSessionCoordinator
  let floorPlan: FloorPlanLayout?
  let rgbEvidenceMode: CameraSessionCoordinator.RGBEvidenceMode
  let sequentialFallbackDetail: String?
  let onScanFace: (ShelfUnit, ShelfFaceSide) -> Void
  let onExceptions: () -> Void
  let onSeal: () -> Void

  @State private var selectedUnitId: UUID?
  @State private var dragStart: CGPoint?

  var body: some View {
    List {
      Section("Camera") {
        Text(camera.statusLine)
        if camera.geometrySessionActive {
          Text("Pass C stills wait until this geometry session has stopped.")
            .font(.footnote)
            .foregroundStyle(.orange)
        }
        if let sequentialFallbackDetail {
          Label(sequentialFallbackDetail, systemImage: "camera.badge.ellipsis")
            .font(.footnote)
            .foregroundStyle(.orange)
        } else if rgbEvidenceMode == .sampledDuringScan {
          Text("Pass A RGB was copied from RoomPlan. Shelf Pass B is a later AR session. Register each unit on the plan so the two spaces meet.")
            .font(.footnote)
            .foregroundStyle(.secondary)
        }
      }
      if let layout = planWithFootprints {
        Section("Register shelves on the RoomPlan plan") {
          Text("Drag a box on the plan for the selected unit. Operator-placed footprints are enough for this demo; they are not measured from the later shelf AR session.")
            .font(.footnote)
            .foregroundStyle(.secondary)
          GeometryReader { geometry in
            TaggedFloorPlanCanvas(layout: layout)
              .contentShape(Rectangle())
              .gesture(
                DragGesture(minimumDistance: 8)
                  .onChanged { value in
                    if dragStart == nil { dragStart = value.startLocation }
                  }
                  .onEnded { value in
                    defer { dragStart = nil }
                    guard let unitId = selectedUnitBinding.wrappedValue else { return }
                    let start = layout.world(from: dragStart ?? value.startLocation, in: geometry.size)
                    let end = layout.world(from: value.location, in: geometry.size)
                    store.setFootprint(
                      unitId: unitId,
                      minX: start.x,
                      minZ: start.z,
                      maxX: end.x,
                      maxZ: end.z
                    )
                  }
              )
              .accessibilityLabel("RoomPlan floor plan. Drag to place the selected shelf footprint.")
          }
          .frame(height: 280)
          .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
          .listRowInsets(EdgeInsets())
          Picker("Unit", selection: selectedUnitBinding) {
            ForEach(store.units) { unit in
              Text(unit.name).tag(Optional(unit.id))
            }
          }
          if let unit = store.units.first(where: { $0.id == selectedUnitBinding.wrappedValue }) {
            Text(
              unit.footprint?.isOperatorPlaced == true
                ? "\(unit.name) is operator-placed on the RoomPlan plan."
                : "\(unit.name) is an unregistered overlay until you drag a box."
            )
            .font(.caption)
            Button("Clear footprint") { store.clearFootprint(unitId: unit.id) }
              .disabled(unit.footprint == nil)
          }
        }
      } else {
        Section("Register shelves on the RoomPlan plan") {
          Text("The RoomPlan floor plan appears here after a processed room scan. Until then, shelf pins stay an unregistered overlay.")
            .font(.footnote)
            .foregroundStyle(.secondary)
        }
      }
      Section("Shelf units") {
        ForEach($store.units) { $unit in
          VStack(alignment: .leading, spacing: 8) {
            TextField("Unit name", text: $unit.name)
            Stepper("Rows: \(unit.rowCount)", value: $unit.rowCount, in: 1...12)
            HStack {
              Button("Scan face A") { onScanFace(unit, .a) }
                .buttonStyle(.borderedProminent)
                .disabled(camera.owner == .roomPlan)
              Button("Scan face B") { onScanFace(unit, .b) }
                .buttonStyle(.bordered)
                .disabled(camera.owner == .roomPlan)
            }
            if let capture = store.captures.first(where: { $0.unitId == unit.id && $0.face == .a }) {
              Text("A: \(capture.frames) frames · \(capture.rows.filter { $0.status == "ok" }.count)/\(capture.rows.count) rows covered")
                .font(.caption)
            }
            if let capture = store.captures.first(where: { $0.unitId == unit.id && $0.face == .b }) {
              Text("B: \(capture.frames) frames · \(capture.rows.filter { $0.status == "ok" }.count)/\(capture.rows.count) rows covered")
                .font(.caption)
            }
          }
          .padding(.vertical, 4)
        }
        Button("Add shelf unit", systemImage: "plus", action: store.addUnit)
      }
      Section {
        Text("The room scan collected geometry only. Review coverage here; scan an uncovered face or row only when needed. Face A and B remain separate. Exception Pass C uses a still camera after RoomPlan and the shelf AR view have stopped.")
          .font(.footnote)
          .foregroundStyle(.secondary)
        Button("Seal Package", systemImage: "lock.fill", action: onSeal)
          .buttonStyle(.borderedProminent)
        Button("Other assets and Exception Pass C", systemImage: "barcode.viewfinder", action: onExceptions)
          .buttonStyle(.bordered)
          .disabled(camera.geometrySessionActive)
      }
    }
    .navigationTitle("Review Shelves")
  }

  private var selectedUnitBinding: Binding<UUID?> {
    Binding(
      get: { selectedUnitId ?? store.units.first?.id },
      set: { selectedUnitId = $0 }
    )
  }

  private var planWithFootprints: FloorPlanLayout? {
    guard var layout = floorPlan else { return nil }
    layout.shelves = store.units.map { unit in
      let box = unit.footprint ?? .unregisteredPlaceholder(face: .a)
      return FloorPlanLayout.ShelfOverlay(
        faceId: unit.faceAId,
        label: unit.name,
        minX: box.minX,
        minZ: box.minZ,
        maxX: box.maxX,
        maxZ: box.maxZ,
        copyCountLabel: unit.name,
        fillLabel: box.isOperatorPlaced ? "operator-placed" : "unregistered overlay",
        status: box.isOperatorPlaced ? "ok" : "partial",
        placement: box.placement
      )
    }
    return layout
  }
}
