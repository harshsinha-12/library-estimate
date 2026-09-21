import Combine
import SwiftUI

struct ShelfPassView: View {
  @ObservedObject var store: ShelfCaptureStore
  let unit: ShelfUnit
  let face: ShelfFaceSide
  let onFinished: () -> Void
  let onFocus: (UIImage, String, Int, Int) -> Void
  let onOther: (UIImage?) -> Void

  var body: some View {
    ZStack(alignment: .bottom) {
      ShelfCameraContainer(store: store)
        .ignoresSafeArea()
      GeometryReader { geometry in
        ForEach(Array(store.highlightedSpines.enumerated()), id: \.offset) { index, box in
          let width = box.width * geometry.size.width
          let height = box.height * geometry.size.height
          Button {
            guard let image = store.focusedImage(for: box) else { return }
            let faceId = face == .a ? unit.faceAId : unit.faceBId
            onFocus(image, faceId, max(1, Int((1 - box.midY) * CGFloat(unit.rowCount)) + 1), index)
          } label: {
            RoundedRectangle(cornerRadius: 5)
              .stroke(.yellow, lineWidth: 3)
              .background(.yellow.opacity(0.08))
              .overlay(alignment: .topLeading) {
                Text("Book?").font(.caption2).padding(2).background(.yellow).foregroundStyle(.black)
              }
          }
          .frame(width: max(44, width), height: max(44, height))
          .position(x: box.midX * geometry.size.width,
                    y: (1 - box.midY) * geometry.size.height)
          .accessibilityLabel("Possible book spine \(index + 1). Tap to inspect close-up")
        }
      }
      .allowsHitTesting(store.capturing)
      VStack(alignment: .leading, spacing: 10) {
        Text("\(unit.name) · face \(face.rawValue)")
          .font(.headline)
        if store.assistCount > 0 {
          Label("Live assist: about \(store.assistCount) visible copies", systemImage: "sparkles")
            .foregroundStyle(.yellow)
        }
        ForEach(store.quality.messages, id: \.self) { message in
          Label(message, systemImage: "exclamationmark.triangle.fill")
            .foregroundStyle(.orange)
        }
        coverageHeatmap
        if !store.recaptureRows.isEmpty {
          Text("Recapture \(store.recaptureRows.joined(separator: ", "))")
            .font(.caption)
            .foregroundStyle(.red)
        }
        HStack {
          Button("Point out object", systemImage: "hand.point.up.left") {
            onOther(store.currentImage())
          }
          .buttonStyle(.bordered)
          if store.capturing {
            Button("Finish face", systemImage: "stop.fill") {
              store.stopFace()
              onFinished()
            }
            .buttonStyle(.borderedProminent)
          } else {
            Button("Start sweep", systemImage: "record.circle") {
              store.startFace(unit: unit, face: face)
            }
            .buttonStyle(.borderedProminent)
          }
        }
      }
      .padding()
      .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 20))
      .padding()
    }
    .navigationTitle("Shelf Pass B")
    .onReceive(Timer.publish(every: 0.4, on: .main, in: .common).autoconnect()) { _ in
      store.ingestCurrentFrame()
    }
  }

  private var coverageHeatmap: some View {
    HStack(spacing: 6) {
      ForEach(store.rowCoverage) { row in
        VStack {
          RoundedRectangle(cornerRadius: 4)
            .fill(row.status == "ok" ? Color.green : Color.orange)
            .opacity(0.35 + row.coverage * 0.65)
            .frame(height: 36)
          Text(row.rowId.replacingOccurrences(of: "row_", with: "R"))
            .font(.caption2)
        }
      }
    }
    .accessibilityLabel("Coverage heatmap by shelf row")
  }
}
