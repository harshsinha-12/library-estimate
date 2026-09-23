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
  @StateObject private var yoloLive = YoloLiveSession()

  var body: some View {
    ZStack(alignment: .bottom) {
      ShelfCameraContainer(store: store, camera: camera)
        .ignoresSafeArea()
        .accessibilityLabel("Live shelf camera and augmented reality view")
        .accessibilityHint("Detected book outlines stay on the spines. Controls stay on the bottom edge.")
      if store.visibleSpines.isEmpty || !store.capturing {
        GeometryReader { geometry in
          ForEach(store.frameOverlays) { item in
            let rect = store.displayRect(for: item.box, in: geometry.size)
            RoundedRectangle(cornerRadius: 2)
              .stroke(item.source == "yolo" ? Color.green : Color.yellow, lineWidth: 1)
              .frame(width: max(8, rect.width), height: max(8, rect.height))
              .position(x: rect.midX, y: rect.midY)
              .allowsHitTesting(false)
              .accessibilityHidden(true)
          }
        }
        .allowsHitTesting(false)
      }
      if store.capturing {
        let finishFace = Button("Finish face", systemImage: "stop.fill") {
          store.stopFace()
          onFinished()
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.small)
        .labelStyle(.iconOnly)
        .accessibilityLabel("Finish face")
        GeometryReader { geometry in
          ForEach(store.visibleSpines) { spine in
            let rect = store.displayRect(for: spine.box, in: geometry.size)
            Button {
              guard let image = store.focusedImage(for: spine.box) else { return }
              onFocus(image, spine.box, face == .a ? unit.faceAId : unit.faceBId,
                      Int(spine.rowId.replacingOccurrences(of: "row_", with: "")) ?? 1,
                      spine.slot)
            } label: {
              RoundedRectangle(cornerRadius: 2)
                .stroke(.yellow, lineWidth: 1)
            }
            .frame(width: max(8, rect.width), height: max(8, rect.height))
            .position(x: rect.midX, y: rect.midY)
            .accessibilityLabel("\(spine.rowId) slot \(spine.slot + 1)")
            .accessibilityValue("Needs Exception Pass C")
            .accessibilityHint("Opens this spine in Exception Pass C")
          }
        }
        VStack(spacing: 4) {
          if !yoloLive.installHint.isEmpty {
            Text(yoloLive.installHint)
              .font(.caption2)
              .lineLimit(1)
              .truncationMode(.tail)
              .foregroundStyle(.white)
          }
          HStack(spacing: 8) {
            ForEach(store.rowCoverage) { row in
              Button(row.rowId.replacingOccurrences(of: "row_", with: "R")) {
                store.selectRow(row.rowId)
              }
              .buttonStyle(.bordered)
              .controlSize(.mini)
              .tint(store.activeRowId == row.rowId ? .blue : .gray)
              .accessibilityLabel("Select \(row.rowId)")
              .accessibilityValue(store.activeRowId == row.rowId ? "Selected" : "Not selected")
            }
            if let row = store.rowCoverage.first(where: { $0.rowId == store.activeRowId }) {
              Text("\(store.instances(for: row.rowId).count)")
                .font(.caption2.bold())
                .monospacedDigit()
                .foregroundStyle(.white)
                .accessibilityLabel("\(store.instances(for: row.rowId).count) candidates")
              Button("Confirm \(row.copyCount)", systemImage: "checkmark") {
                store.confirmActualCount(for: row.rowId)
              }
              .labelStyle(.iconOnly)
              .buttonStyle(.bordered)
              .controlSize(.mini)
              .accessibilityLabel("Confirm \(row.copyCount)")
            }
            finishFace
          }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(.black.opacity(0.35), in: Capsule())
        .padding(.horizontal, 8)
        .padding(.bottom, 8)
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
          AccessibleStatusLabel(text: astraLive.caption, kind: .progress)
            .font(.caption)
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
    .toolbar(store.capturing ? .hidden : .automatic, for: .navigationBar)
    .accessibilityStatusAnnouncements(accessibilityCaptureStatus)
    .onReceive(Timer.publish(every: 0.4, on: .main, in: .common).autoconnect()) { _ in
      store.ingestCurrentFrame()
      if let jpeg = store.currentJpeg() {
        Task {
          await yoloLive.consider(jpeg: jpeg, backendURL: backendURL)
          if yoloLive.enabled, !yoloLive.boxes.isEmpty {
            store.applyYoloOverlays(yoloLive.boxes)
          }
        }
      }
      if store.capturing {
        if let jpeg = store.currentJpeg(), let backendURL {
          Task { await livePrices.consider(jpeg: jpeg, draft: draft, backendURL: backendURL) }
        }
        Task {
          await astraLive.consider(
            jpeg: store.currentJpeg(),
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
