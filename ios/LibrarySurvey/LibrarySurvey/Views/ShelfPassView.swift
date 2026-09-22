import Combine
import SwiftUI

struct ShelfPassView: View {
  @ObservedObject var store: ShelfCaptureStore
  @ObservedObject var camera: CameraSessionCoordinator
  let unit: ShelfUnit
  let face: ShelfFaceSide
  let draft: SurveyDraft
  let onFinished: () -> Void
  let onFocus: (UIImage, String, Int, Int) -> Void
  let onOther: (UIImage?) -> Void
  @AppStorage("backendURL") private var backendURLString = "http://192.168.29.178:8000"
  @StateObject private var livePrices = LivePriceSession()

  var body: some View {
    ZStack(alignment: .bottom) {
      ShelfCameraContainer(store: store, camera: camera)
        .ignoresSafeArea()
      if store.capturing {
        GeometryReader { geometry in
          RoundedRectangle(cornerRadius: 8)
            .stroke(.cyan.opacity(0.6), style: StrokeStyle(lineWidth: 2, dash: [8, 5]))
            .frame(width: geometry.size.width * 0.94, height: geometry.size.height * 0.44)
            .position(x: geometry.size.width / 2, y: geometry.size.height / 2)
            .allowsHitTesting(false)
          ForEach(store.visibleSpines) { spine in
            let rect = store.displayRect(for: spine.box, in: geometry.size)
            let highlight = livePrices.highlight(for: spine.box)
            let color: Color = {
              switch highlight?.status {
              case "draft": .green
              case "unresolved": .orange
              default: .yellow
              }
            }()
            Button {
              guard let image = store.focusedImage(for: spine.box) else { return }
              onFocus(image, face == .a ? unit.faceAId : unit.faceBId,
                      Int(spine.rowId.replacingOccurrences(of: "row_", with: "")) ?? 1,
                      spine.slot)
            } label: {
              RoundedRectangle(cornerRadius: 4)
                .stroke(color, lineWidth: highlight == nil ? 2 : 3)
                .background(color.opacity(0.12))
                .overlay(alignment: .top) {
                  Text(highlight?.caption ?? "Slot \(spine.slot + 1)")
                    .font(.caption2.bold())
                    .lineLimit(2)
                    .padding(.horizontal, 4)
                    .padding(.vertical, 2)
                    .background(color)
                    .foregroundStyle(.black)
                    .clipShape(RoundedRectangle(cornerRadius: 3))
                }
            }
            .frame(width: max(44, rect.width), height: max(44, rect.height))
            .position(x: rect.midX, y: rect.midY)
            .accessibilityLabel("\(spine.rowId) slot \(spine.slot + 1), tap for Pass C")
          }
        }
        VStack(spacing: 8) {
          Text("Center the selected row in the guide. Reverse over the same row to add evidence.")
            .font(.caption)
          HStack {
            ForEach(store.rowCoverage) { row in
              Button(row.rowId.replacingOccurrences(of: "row_", with: "R")) {
                store.selectRow(row.rowId)
              }
              .buttonStyle(.bordered)
              .tint(store.activeRowId == row.rowId ? .blue : .gray)
            }
          }
          .frame(maxWidth: .infinity)
          Text("\(store.activeRowId): \(store.instances(for: store.activeRowId).count) persistent candidates")
            .font(.caption.bold())
          if let row = store.rowCoverage.first(where: { $0.rowId == store.activeRowId }) {
            Text("Readable coverage \(Int(row.coverage * 100))% · \(row.status). Count \(row.copyCount) / actual \(row.actualCount.map(String.init) ?? "unconfirmed").")
              .font(.caption2)
            HStack {
              Stepper("Actual \(row.actualCount ?? row.copyCount)", value: Binding(
                get: { row.actualCount ?? row.copyCount },
                set: { store.setActualCount($0, for: row.rowId) }
              ), in: 0...40)
              Button("Confirm \(row.copyCount)") {
                store.confirmActualCount(for: row.rowId)
              }
              .buttonStyle(.bordered)
            }
          }
          if let warning = store.quality.messages.first {
            Label(warning, systemImage: "exclamationmark.triangle")
              .font(.caption)
              .foregroundStyle(.orange)
          }
          ScrollView(.horizontal) {
            HStack(spacing: 8) {
              ForEach(Array(store.instances(for: store.activeRowId).enumerated()), id: \.element.id) { slot, instance in
                Button {
                  guard let image = store.evidenceImage(for: instance) else { return }
                  onFocus(image, face == .a ? unit.faceAId : unit.faceBId,
                          Int(instance.rowId.replacingOccurrences(of: "row_", with: "")) ?? 1,
                          slot)
                } label: {
                  VStack {
                    if let image = store.evidenceImage(for: instance) {
                      Image(uiImage: image).resizable().scaledToFit().frame(width: 42, height: 56)
                    }
                    Text("\(slot + 1)\(instance.hasReadableText ? "" : " ?")")
                      .font(.caption2)
                  }
                }
                .accessibilityLabel("Saved spine slot \(slot + 1), \(instance.hasReadableText ? "readable" : "needs Pass C")")
              }
            }
          }
          Button("Finish face", systemImage: "stop.fill") {
            store.stopFace()
            onFinished()
          }
          .buttonStyle(.borderedProminent)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16))
        .padding()
      } else {
        VStack(alignment: .leading, spacing: 10) {
          Text("\(unit.name) · face \(face.rawValue)")
            .font(.headline)
          Text(camera.statusLine)
            .font(.caption)
            .foregroundStyle(.secondary)
          if camera.owner == .roomPlan {
            Label(
              "Waiting for RoomPlan to release the camera. Pass B does not start a second session.",
              systemImage: "camera.fill"
            )
            .font(.caption)
            .foregroundStyle(.orange)
          }
          if store.assistCount > 0 {
            Label("About \(store.assistCount) visible copies", systemImage: "sparkles")
              .foregroundStyle(.yellow)
          }
          Text("Coverage marks distinct readable regions of the selected row. Confirm the actual count before sealing; any mismatch stays partial.")
            .font(.caption)
          coverageHeatmap
          HStack {
            Button("Point out object", systemImage: "hand.point.up.left") {
              onOther(store.currentImage())
            }
            .buttonStyle(.bordered)
            Button("Start sweep", systemImage: "record.circle") {
              store.startFace(unit: unit, face: face)
            }
            .buttonStyle(.borderedProminent)
            .disabled(camera.owner != .shelfAR)
          }
          Text(unit.footprint?.isOperatorPlaced == true
            ? "This face is registered with an operator-placed footprint on the RoomPlan plan."
            : "Unregistered overlay: place this unit on the RoomPlan plan in Review Shelves. Shelf RGB poses are from this later AR session.")
            .font(.caption2)
            .foregroundStyle(.secondary)
        }
        .padding()
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20))
        .padding()
      }
    }
    .navigationBarTitleDisplayMode(.inline)
    .onReceive(Timer.publish(every: 0.4, on: .main, in: .common).autoconnect()) { _ in
      store.ingestCurrentFrame()
      if store.capturing, let jpeg = store.currentJpeg(), let backendURL {
        Task { await livePrices.consider(jpeg: jpeg, draft: draft, backendURL: backendURL) }
      }
    }
  }

  private var backendURL: URL? {
    URL(string: backendURLString.trimmingCharacters(in: .whitespacesAndNewlines))
  }

  private var coverageHeatmap: some View {
    HStack(spacing: 6) {
        ForEach(Array(store.rowCoverage.indices), id: \.self) { index in
          VStack {
            RoundedRectangle(cornerRadius: 4)
              .fill(store.rowCoverage[index].status == "ok" ? Color.green : Color.orange)
              .opacity(0.35 + store.rowCoverage[index].coverage * 0.65)
              .frame(height: 36)
            Text(store.rowCoverage[index].rowId.replacingOccurrences(of: "row_", with: "R"))
              .font(.caption2)
            Stepper(
              "Actual \(store.rowCoverage[index].actualCount.map(String.init) ?? "unconfirmed")",
              value: Binding(
                get: { store.rowCoverage[index].actualCount ?? store.rowCoverage[index].copyCount },
                set: { store.setActualCount($0, for: store.rowCoverage[index].rowId) }
              ),
              in: 0...40
            )
            .font(.caption2)
            .labelsHidden()
            Text("det \(store.rowCoverage[index].copyCount)")
              .font(.caption2)
            Button("Confirm \(store.rowCoverage[index].copyCount)") {
              store.confirmActualCount(for: store.rowCoverage[index].rowId)
            }
            .font(.caption2)
          }
        }
    }
    .accessibilityLabel("Readable coverage and confirmed count by shelf row")
  }
}
