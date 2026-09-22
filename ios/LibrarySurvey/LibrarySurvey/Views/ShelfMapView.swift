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
          .accessibilityLabel("Camera status")
          .accessibilityValue(camera.statusLine)
        if camera.geometrySessionActive {
          AccessibleStatusLabel(
            text: "Pass C stills wait until this geometry session has stopped.",
            kind: .warning
          )
          .font(.footnote)
        }
        if let sequentialFallbackDetail {
          AccessibleStatusLabel(text: sequentialFallbackDetail, kind: .warning)
            .font(.footnote)
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
              .accessibilityHint("Drag placement is visual. Select a shelf below to hear whether it is registered; physical placement requires sighted assistance.")
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
              .minimumScaledTouchTarget()
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
            ViewThatFits {
              HStack {
                scanButton(unit, face: .a)
                scanButton(unit, face: .b)
              }
              VStack(alignment: .leading) {
                scanButton(unit, face: .a)
                scanButton(unit, face: .b)
              }
            }
            if let capture = store.captures.first(where: { $0.unitId == unit.id && $0.face == .a }) {
              AccessibleStatusLabel(
                text: "Face A: \(capture.frames) frames · \(capture.rows.filter { $0.status == "ok" }.count)/\(capture.rows.count) rows covered",
                kind: capture.rows.allSatisfy { $0.status == "ok" } ? .success : .warning
              )
                .font(.caption)
            }
            if let capture = store.captures.first(where: { $0.unitId == unit.id && $0.face == .b }) {
              AccessibleStatusLabel(
                text: "Face B: \(capture.frames) frames · \(capture.rows.filter { $0.status == "ok" }.count)/\(capture.rows.count) rows covered",
                kind: capture.rows.allSatisfy { $0.status == "ok" } ? .success : .warning
              )
                .font(.caption)
            }
          }
          .padding(.vertical, 4)
        }
        Button("Add shelf unit", systemImage: "plus", action: store.addUnit)
          .minimumScaledTouchTarget()
      }
      Section {
        Text("The room scan collected geometry only. Review coverage here; scan an uncovered face or row only when needed. Face A and B remain separate. Exception Pass C uses a still camera after RoomPlan and the shelf AR view have stopped.")
          .font(.footnote)
          .foregroundStyle(.secondary)
        Button("Seal Package", systemImage: "lock.fill", action: onSeal)
          .buttonStyle(.borderedProminent)
          .minimumScaledTouchTarget()
        Button("Other assets and Exception Pass C", systemImage: "barcode.viewfinder", action: onExceptions)
          .buttonStyle(.bordered)
          .disabled(camera.geometrySessionActive)
          .minimumScaledTouchTarget()
      }
    }
    .navigationTitle("Review Shelves")
    .accessibilityStatusAnnouncements(camera.statusLine)
  }

  @ViewBuilder
  private func scanButton(_ unit: ShelfUnit, face: ShelfFaceSide) -> some View {
    if face == .a {
      Button("Scan face A") { onScanFace(unit, face) }
        .buttonStyle(.borderedProminent)
        .disabled(camera.owner == .roomPlan)
        .minimumScaledTouchTarget()
        .accessibilityHint("Starts Shelf Pass B for \(unit.name), face A")
    } else {
      Button("Scan face B") { onScanFace(unit, face) }
        .buttonStyle(.bordered)
        .disabled(camera.owner == .roomPlan)
        .minimumScaledTouchTarget()
        .accessibilityHint("Starts Shelf Pass B for \(unit.name), face B")
    }
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
