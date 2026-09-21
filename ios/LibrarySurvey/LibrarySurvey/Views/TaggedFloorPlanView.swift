import SceneKit
import SwiftUI

struct TaggedFloorPlanView: View {
  let layout: FloorPlanLayout

  var body: some View {
    VStack(alignment: .leading, spacing: 12) {
      ZStack(alignment: .topTrailing) {
        TaggedFloorPlanCanvas(layout: layout)
          .frame(height: 280)
          .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
        Text("N")
          .font(.caption.weight(.bold))
          .foregroundStyle(.white)
          .padding(.trailing, 16)
          .padding(.top, 12)
          .accessibilityLabel("North is scan plus Z, not magnetic north")
      }
      Text(layout.summaryLine)
        .font(.footnote)
        .foregroundStyle(.secondary)
      Text("Numbers match the list. N is scan +Z, not magnetic north.")
        .font(.caption2)
        .foregroundStyle(.tertiary)
      legend(title: "Walls", rows: layout.walls.map { wall in
        LegendRow(
          color: Color(floorPlanHex: wall.colorHex),
          text: "Wall \(wall.index)  \(wall.lengthCm) cm · \(wall.compass)"
        )
      })
      openingLegend(kind: .door, title: "Doors")
      openingLegend(kind: .window, title: "Windows")
          openingLegend(kind: .opening, title: "Openings")
      if !layout.shelves.isEmpty {
        legend(title: "Shelves", rows: layout.shelves.map { shelf in
          LegendRow(
            color: shelf.placement == ShelfFootprint.operatorKind ? Color.blue : Color.orange,
            text: "\(shelf.label)  \(shelf.copyCountLabel)  \(shelf.fillLabel)  \(shelf.placementLabel)"
          )
        })
      }
    }
  }

  @ViewBuilder
  private func openingLegend(kind: FloorPlanLayout.Opening.Kind, title: String) -> some View {
    let items = layout.openings.filter { $0.kind == kind }
    if !items.isEmpty {
      legend(title: title, rows: items.map { opening in
        let parent = opening.wallIndex.map { " · on wall \($0)" } ?? ""
        let label = title.dropLast()
        return LegendRow(
          color: Color(floorPlanHex: opening.colorHex),
          text: "\(label) \(opening.index)  \(opening.lengthCm) cm\(parent)"
        )
      })
    }
  }

  private func legend(title: String, rows: [LegendRow]) -> some View {
    VStack(alignment: .leading, spacing: 6) {
      Text(title)
        .font(.subheadline.weight(.semibold))
      ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
        HStack(spacing: 8) {
          Circle()
            .fill(row.color)
            .frame(width: 8, height: 8)
          Text(row.text)
            .font(.footnote.monospacedDigit())
        }
      }
    }
  }
}

private struct LegendRow {
  let color: Color
  let text: String
}

struct TaggedFloorPlanCanvas: View {
  let layout: FloorPlanLayout

  var body: some View {
    Canvas { context, size in
      let background = Path(CGRect(origin: .zero, size: size))
      context.fill(background, with: .color(Color(red: 0.11, green: 0.11, blue: 0.12)))
      for wall in layout.walls {
        var path = Path()
        path.move(to: layout.screen(wall.start, in: size))
        path.addLine(to: layout.screen(wall.end, in: size))
        context.stroke(
          path,
          with: .color(Color(floorPlanHex: wall.colorHex)),
          style: StrokeStyle(lineWidth: 7, lineCap: .square)
        )
      }
      for shelf in layout.shelves {
        let a = layout.screen(FloorPlanLayout.Point(x: shelf.minX, z: shelf.maxZ), in: size)
        let b = layout.screen(FloorPlanLayout.Point(x: shelf.maxX, z: shelf.minZ), in: size)
        let rect = CGRect(
          x: min(a.x, b.x),
          y: min(a.y, b.y),
          width: abs(b.x - a.x),
          height: abs(b.y - a.y)
        )
        let color = shelf.placement == ShelfFootprint.operatorKind
          ? Color.blue.opacity(0.35)
          : Color.orange.opacity(0.4)
        context.fill(Path(roundedRect: rect, cornerRadius: 4), with: .color(color))
        context.draw(
          Text(shelf.copyCountLabel).font(.caption2).foregroundColor(.white),
          at: CGPoint(x: rect.midX, y: rect.midY)
        )
      }
      for opening in layout.openings {
        var path = Path()
        path.move(to: layout.screen(opening.start, in: size))
        path.addLine(to: layout.screen(opening.end, in: size))
        context.stroke(
          path,
          with: .color(Color(floorPlanHex: opening.colorHex)),
          style: StrokeStyle(lineWidth: 9, lineCap: .butt)
        )
      }
      for wall in layout.walls {
        let mid = layout.screen(wall.midpoint, in: size)
        let offset = layout.labelOffset(for: wall)
        let point = CGPoint(x: mid.x + offset.width, y: mid.y + offset.height)
        context.draw(
          Text("\(wall.index)").font(.caption.weight(.bold)).foregroundColor(Color(floorPlanHex: wall.colorHex)),
          at: point
        )
      }
    }
    .accessibilityLabel("Tagged floor plan with numbered walls and openings")
  }
}

struct RoomModelPreview: UIViewRepresentable {
  let url: URL

  func makeUIView(context: Context) -> SCNView {
    let view = SCNView()
    view.autoenablesDefaultLighting = true
    view.allowsCameraControl = true
    view.backgroundColor = UIColor(white: 0.16, alpha: 1)
    view.antialiasingMode = .multisampling2X
    view.rendersContinuously = false
    view.scene = try? SCNScene(url: url, options: [.checkConsistency: false])
    return view
  }

  func updateUIView(_ uiView: SCNView, context: Context) {}

  static func dismantleUIView(_ uiView: SCNView, coordinator: ()) {
    uiView.scene = nil
    uiView.pause(nil)
  }
}
