import Combine
import SwiftUI

struct ShelfPassView: View {
  @ObservedObject var store: ShelfCaptureStore
  @ObservedObject var camera: CameraSessionCoordinator
  let unit: ShelfUnit
  let face: ShelfFaceSide
  let draft: SurveyDraft
  let onFinished: () -> Void
  let onFocus: (UIImage, CGRect, String, Int, Int) -> Void
  let onOther: (UIImage?) -> Void
  @AppStorage("backendURL") private var backendURLString = "http://192.168.29.178:8000"
  @StateObject private var livePrices = LivePriceSession()
  @StateObject private var astraLive = AstraLiveSession()

  var body: some View {
    ZStack(alignment: .bottom) {
      ShelfCameraContainer(store: store, camera: camera)
        .ignoresSafeArea()
        .accessibilityLabel("Live shelf camera and augmented reality view")
        .accessibilityHint("Center the selected shelf row in the guide. Detected spine controls are listed as accessibility elements.")
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
              onFocus(image, spine.box, face == .a ? unit.faceAId : unit.faceBId,
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
            .accessibilityLabel("\(spine.rowId) slot \(spine.slot + 1)")
            .accessibilityValue(highlight?.caption ?? "Needs review")
            .accessibilityHint("Opens this spine in Exception Pass C")
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
              .minimumScaledTouchTarget()
              .accessibilityLabel("Select \(row.rowId)")
              .accessibilityValue(store.activeRowId == row.rowId ? "Selected" : "Not selected")
            }
          }
          .frame(maxWidth: .infinity)
          Text("\(store.activeRowId): \(store.instances(for: store.activeRowId).count) persistent candidates")
            .font(.caption.bold())
          if let row = store.rowCoverage.first(where: { $0.rowId == store.activeRowId }) {
            Text("Readable coverage \(Int(row.coverage * 100))% · \(row.status). Count \(row.copyCount) / actual \(row.actualCount.map(String.init) ?? "unconfirmed").")
              .font(.caption2)
            ViewThatFits {
              HStack {
                actualCountStepper(row)
                confirmCountButton(row)
              }
              VStack(alignment: .leading) {
                actualCountStepper(row)
                confirmCountButton(row)
              }
            }
          }
          if let warning = store.quality.messages.first {
            AccessibleStatusLabel(text: warning, kind: .warning)
              .font(.caption)
          }
          ScrollView(.horizontal) {
            HStack(spacing: 8) {
              ForEach(Array(store.instances(for: store.activeRowId).enumerated()), id: \.element.id) { slot, instance in
                Button {
                  guard let image = store.evidenceImage(for: instance) ?? store.currentImage() else { return }
                  onFocus(image, instance.box, face == .a ? unit.faceAId : unit.faceBId,
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
                .minimumScaledTouchTarget()
                .accessibilityLabel("Saved spine slot \(slot + 1)")
                .accessibilityValue(instance.hasReadableText ? "Readable" : "Needs Exception Pass C")
                .accessibilityHint("Opens evidence for this spine")
              }
            }
          }
          Button("Finish face", systemImage: "stop.fill") {
            store.stopFace()
            onFinished()
          }
          .buttonStyle(.borderedProminent)
          .minimumScaledTouchTarget()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 16))
        .padding()
        if !astraLive.status.isEmpty {
          AccessibleStatusLabel(text: astraLive.caption, kind: .progress)
            .font(.caption2)
            .padding(8)
            .background(.ultraThinMaterial, in: Capsule())
            .padding(.bottom, 72)
        }
      } else {
        VStack(alignment: .leading, spacing: 10) {
          Text("\(unit.name) · face \(face.rawValue)")
            .font(.headline)
          Text(camera.statusLine)
            .font(.caption)
            .foregroundStyle(.secondary)
            .accessibilityLabel("Camera status")
            .accessibilityValue(camera.statusLine)
          if camera.owner == .roomPlan {
            AccessibleStatusLabel(
              text: "Waiting for RoomPlan to release the camera. Pass B does not start a second session.",
              kind: .warning
            )
            .font(.caption)
          }
          if store.assistCount > 0 {
            AccessibleStatusLabel(text: "About \(store.assistCount) visible copies", kind: .neutral)
          }
          if !astraLive.status.isEmpty {
            AccessibleStatusLabel(text: astraLive.caption, kind: .progress)
              .font(.caption)
          }
          Text("Coverage marks distinct readable regions of the selected row. Confirm the actual count before sealing; any mismatch stays partial.")
            .font(.caption)
          coverageHeatmap
          ViewThatFits {
            HStack {
              pointOutButton
              startSweepButton
            }
            VStack(alignment: .leading) {
              pointOutButton
              startSweepButton
            }
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
    .accessibilityStatusAnnouncements(accessibilityCaptureStatus)
    .onReceive(Timer.publish(every: 0.4, on: .main, in: .common).autoconnect()) { _ in
      store.ingestCurrentFrame()
      if store.capturing, let jpeg = store.currentJpeg(), let backendURL {
        Task { await livePrices.consider(jpeg: jpeg, draft: draft, backendURL: backendURL) }
        Task {
          await astraLive.consider(
            jpeg: jpeg,
            capturePass: "B",
            draft: draft,
            backendURL: backendURL,
            qualityMessages: store.quality.messages,
            provisionalCount: store.assistCount,
            unreadableSlots: store.recaptureRows
          )
        }
      }
    }
    .onChange(of: store.capturing) { _, capturing in
      if capturing { astraLive.resetFace() }
    }
  }

  private func actualCountStepper(_ row: ShelfRowCoverage) -> some View {
    Stepper("Actual \(row.actualCount ?? row.copyCount)", value: Binding(
      get: { row.actualCount ?? row.copyCount },
      set: { store.setActualCount($0, for: row.rowId) }
    ), in: 0...40)
    .minimumScaledTouchTarget()
    .accessibilityHint("Adjust the verified number of physical copies on this row")
  }

  private func confirmCountButton(_ row: ShelfRowCoverage) -> some View {
    Button("Confirm \(row.copyCount)") {
      store.confirmActualCount(for: row.rowId)
    }
    .buttonStyle(.bordered)
    .minimumScaledTouchTarget()
    .accessibilityHint("Confirms the detected count as the actual count")
  }

  private var pointOutButton: some View {
    Button("Point out object", systemImage: "hand.point.up.left") {
      onOther(store.currentImage())
    }
    .buttonStyle(.bordered)
    .minimumScaledTouchTarget()
  }

  private var startSweepButton: some View {
    Button("Start sweep", systemImage: "record.circle") {
      store.startFace(unit: unit, face: face)
    }
    .buttonStyle(.borderedProminent)
    .minimumScaledTouchTarget()
    .disabled(camera.owner != .shelfAR)
  }

  private var accessibilityCaptureStatus: String {
    if store.capturing {
      return "Shelf capture active for \(unit.name), face \(face.rawValue), \(store.activeRowId)"
    }
    return camera.owner == .roomPlan
      ? "Shelf capture waiting for the room camera"
      : "Shelf capture ready for \(unit.name), face \(face.rawValue)"
  }

  private var backendURL: URL? {
    URL(string: backendURLString.trimmingCharacters(in: .whitespacesAndNewlines))
  }

  private var coverageHeatmap: some View {
    ScrollView(.horizontal) {
      HStack(spacing: 12) {
        ForEach(Array(store.rowCoverage.indices), id: \.self) { index in
          VStack(alignment: .leading) {
            RoundedRectangle(cornerRadius: 4)
              .fill(store.rowCoverage[index].status == "ok" ? Color.green : Color.orange)
              .opacity(0.35 + store.rowCoverage[index].coverage * 0.65)
              .frame(height: 36)
            Text(store.rowCoverage[index].rowId.replacingOccurrences(of: "row_", with: "R"))
              .font(.caption2)
            Label(
              store.rowCoverage[index].status == "ok" ? "Covered" : "Needs review",
              systemImage: store.rowCoverage[index].status == "ok"
                ? "checkmark.circle.fill"
                : "exclamationmark.triangle.fill"
            )
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
            .minimumScaledTouchTarget()
            Text("det \(store.rowCoverage[index].copyCount)")
              .font(.caption2)
            Button("Confirm \(store.rowCoverage[index].copyCount)") {
              store.confirmActualCount(for: store.rowCoverage[index].rowId)
            }
            .font(.caption2)
            .minimumScaledTouchTarget()
          }
        }
      }
    }
  }
}
