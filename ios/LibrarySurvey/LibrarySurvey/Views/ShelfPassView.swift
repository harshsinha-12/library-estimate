import Combine
import SwiftUI

struct ShelfPassView: View {
  @ObservedObject var store: ShelfCaptureStore
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
      ShelfCameraContainer(store: store)
        .ignoresSafeArea()
      GeometryReader { geometry in
        ForEach(Array(store.highlightedSpines.enumerated()), id: \.offset) { index, box in
          let rect = store.displayRect(for: box, in: geometry.size)
          let highlight = livePrices.highlight(for: box)
          let color: Color = {
            switch highlight?.status {
            case "draft": .green
            case "unresolved": .orange
            default: .yellow
            }
          }()
          RoundedRectangle(cornerRadius: 4)
            .stroke(color, lineWidth: highlight == nil ? 2 : 3)
            .background(color.opacity(0.12))
            .overlay(alignment: .top) {
              if let caption = highlight?.caption {
                Text(caption)
                  .font(.caption2.bold())
                  .lineLimit(2)
                  .padding(.horizontal, 4)
                  .padding(.vertical, 2)
                  .background(color)
                  .foregroundStyle(.black)
                  .clipShape(RoundedRectangle(cornerRadius: 3))
              }
            }
            .frame(width: max(8, rect.width), height: max(16, rect.height))
            .position(x: rect.midX, y: rect.midY)
            .allowsHitTesting(false)
            .accessibilityLabel("Book copy \(index + 1)")
        }
      }
      .allowsHitTesting(false)
      if store.capturing {
        Button("Finish face", systemImage: "stop.fill") {
          store.stopFace()
          onFinished()
        }
        .buttonStyle(.borderedProminent)
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(.ultraThinMaterial, in: Capsule())
        .padding()
      } else {
        VStack(alignment: .leading, spacing: 10) {
          Text("\(unit.name) · face \(face.rawValue)")
            .font(.headline)
          if store.assistCount > 0 {
            Label("About \(store.assistCount) visible copies", systemImage: "sparkles")
              .foregroundStyle(.yellow)
          }
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
          }
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
              "Actual \(store.rowCoverage[index].actualCount ?? store.rowCoverage[index].copyCount)",
              value: Binding(
                get: { store.rowCoverage[index].actualCount ?? store.rowCoverage[index].copyCount },
                set: { store.rowCoverage[index].actualCount = $0 }
              ),
              in: 0...40
            )
            .font(.caption2)
            .labelsHidden()
            Text("det \(store.rowCoverage[index].copyCount)")
              .font(.caption2)
          }
        }
    }
    .accessibilityLabel("Coverage heatmap by shelf row")
  }
}
