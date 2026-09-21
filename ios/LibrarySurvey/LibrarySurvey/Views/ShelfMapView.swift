import SwiftUI

struct ShelfMapView: View {
  @ObservedObject var store: ShelfCaptureStore
  let onScanFace: (ShelfUnit, ShelfFaceSide) -> Void
  let onExceptions: () -> Void
  let onSeal: () -> Void

  var body: some View {
    List {
      Section("Shelf units") {
        ForEach($store.units) { $unit in
          VStack(alignment: .leading, spacing: 8) {
            TextField("Unit name", text: $unit.name)
            Stepper("Rows: \(unit.rowCount)", value: $unit.rowCount, in: 1...12)
            HStack {
              Button("Scan face A") { onScanFace(unit, .a) }
                .buttonStyle(.borderedProminent)
              Button("Scan face B") { onScanFace(unit, .b) }
                .buttonStyle(.bordered)
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
        Text("The room scan collected face A candidates. Review coverage here; scan an uncovered face or row only when needed. Face A and B remain separate.")
          .font(.footnote)
          .foregroundStyle(.secondary)
        Button("Seal Package", systemImage: "lock.fill", action: onSeal)
          .buttonStyle(.borderedProminent)
        Button("Other assets and Exception Pass C", systemImage: "barcode.viewfinder", action: onExceptions)
          .buttonStyle(.bordered)
      }
    }
    .navigationTitle("Review Shelves")
  }
}
