import Foundation
import SwiftUI

struct FloorPlanLayout {
  struct Point: Equatable {
    var x: Double
    var z: Double
  }

  struct Wall: Equatable, Identifiable {
    var id: Int { index }
    let index: Int
    let identifier: String
    let start: Point
    let end: Point
    let lengthMetres: Double
    let heightMetres: Double
    let compass: String
    let colorHex: String

    var lengthCm: Int { Int((lengthMetres * 100).rounded()) }
    var midpoint: Point { Point(x: (start.x + end.x) / 2, z: (start.z + end.z) / 2) }
  }

  struct Opening: Equatable, Identifiable {
    var id: String { "\(kind.rawValue)-\(index)" }
    let index: Int
    let kind: Kind
    let start: Point
    let end: Point
    let lengthMetres: Double
    let wallIndex: Int?
    let colorHex: String

    var lengthCm: Int { Int((lengthMetres * 100).rounded()) }

    enum Kind: String {
      case door
      case window
      case opening
    }
  }

  struct ShelfOverlay: Identifiable, Equatable {
    var id: String { faceId }
    let faceId: String
    let label: String
    let minX: Double
    let minZ: Double
    let maxX: Double
    let maxZ: Double
    let copyCountLabel: String
    let fillLabel: String
    let status: String
  }

  static let palette = [
    "#3B7BFF", "#FF8C33", "#33C766", "#E040A8",
    "#8C59F2", "#26BFBF", "#F2C14E", "#FF5C5C"
  ]

  let roomLabels: [String]
  let walls: [Wall]
  let openings: [Opening]
  let minX: Double
  let maxX: Double
  let minZ: Double
  let maxZ: Double
  let floorAreaSquareMetres: Double
  let ceilingHeightMetres: Double?
  var shelves: [ShelfOverlay] = []

  var ceilingHeightCm: Int? {
    ceilingHeightMetres.map { Int(($0 * 100).rounded()) }
  }

  var summaryLine: String {
    let ceiling = ceilingHeightCm.map { "ceiling \($0) cm" } ?? "ceiling unknown"
    return "\(walls.count) walls · \(String(format: "%.1f", floorAreaSquareMetres)) m² · \(ceiling)"
  }

  init(_ structure: PortableStructure) throws {
    var walls: [Wall] = []
    var openings: [Opening] = []
    var heights: [Double] = []
    var pending: [(PortableRoom, [Wall])] = []
    for room in structure.rooms {
      var roomWalls: [Wall] = []
      for surface in room.walls {
        let ends = try Self.segment(surface)
        let index = walls.count + 1
        let wall = Wall(
          index: index,
          identifier: surface.identifier,
          start: ends.0,
          end: ends.1,
          lengthMetres: Double(surface.dimensions[0]),
          heightMetres: Double(surface.dimensions[1]),
          compass: "N",
          colorHex: Self.palette[(index - 1) % Self.palette.count]
        )
        walls.append(wall)
        roomWalls.append(wall)
        heights.append(wall.heightMetres)
      }
      pending.append((room, roomWalls))
    }
    if !walls.isEmpty {
      let centroid = Point(
        x: walls.map(\.midpoint.x).reduce(0, +) / Double(walls.count),
        z: walls.map(\.midpoint.z).reduce(0, +) / Double(walls.count)
      )
      walls = walls.map { wall in
        Wall(
          index: wall.index,
          identifier: wall.identifier,
          start: wall.start,
          end: wall.end,
          lengthMetres: wall.lengthMetres,
          heightMetres: wall.heightMetres,
          compass: Self.outwardCompass(wall, centroid: centroid),
          colorHex: wall.colorHex
        )
      }
      pending = pending.map { room, roomWalls in
        (room, roomWalls.compactMap { original in walls.first { $0.index == original.index } })
      }
    }
    for (room, roomWalls) in pending {
      let grouped: [(Opening.Kind, [PortableSurface])] = [
        (.door, room.doors),
        (.window, room.windows),
        (.opening, room.openings)
      ]
      for (kind, surfaces) in grouped {
        for surface in surfaces {
          guard let ends = try? Self.segment(surface) else { continue }
          let count = openings.filter { $0.kind == kind }.count
          openings.append(
            Opening(
              index: count + 1,
              kind: kind,
              start: ends.0,
              end: ends.1,
              lengthMetres: Double(surface.dimensions[0]),
              wallIndex: Self.parentWallIndex(surface, start: ends.0, end: ends.1, walls: roomWalls),
              colorHex: Self.openingColor(kind)
            )
          )
        }
      }
    }
    guard !walls.isEmpty else {
      throw PackageWriterError.invalidGeometry("RoomPlan returned no walls.")
    }
    let xs = walls.flatMap { [$0.start.x, $0.end.x] }
    let zs = walls.flatMap { [$0.start.z, $0.end.z] }
    minX = xs.min() ?? 0
    maxX = xs.max() ?? 1
    minZ = zs.min() ?? 0
    maxZ = zs.max() ?? 1
    floorAreaSquareMetres = Self.floorArea(walls: walls, minX: minX, maxX: maxX, minZ: minZ, maxZ: maxZ)
    ceilingHeightMetres = heights.max()
    roomLabels = structure.rooms.map(\.label)
    self.walls = walls
    self.openings = openings
  }

  static func load(from url: URL) throws -> FloorPlanLayout {
    let data = try Data(contentsOf: url)
    let structure = try JSONCoding.decoder().decode(PortableStructure.self, from: data)
    return try FloorPlanLayout(structure)
  }

  func screen(_ point: Point, in size: CGSize, padding: Double = 28) -> CGPoint {
    let width = max(maxX - minX, 0.01)
    let depth = max(maxZ - minZ, 0.01)
    let scale = min(
      (Double(size.width) - padding * 2) / width,
      (Double(size.height) - padding * 2) / depth
    )
    return CGPoint(
      x: (point.x - minX) * scale + padding,
      y: (maxZ - point.z) * scale + padding
    )
  }

  func labelOffset(for wall: Wall, scale: Double = 1) -> CGSize {
    let centroid = Point(
      x: walls.map(\.midpoint.x).reduce(0, +) / Double(walls.count),
      z: walls.map(\.midpoint.z).reduce(0, +) / Double(walls.count)
    )
    var px = -(wall.end.z - wall.start.z)
    var pz = wall.end.x - wall.start.x
    let length = (px * px + pz * pz).squareRoot()
    if length > 0 {
      px /= length
      pz /= length
    }
    let mid = wall.midpoint
    if (mid.x - centroid.x) * px + (mid.z - centroid.z) * pz < 0 {
      px = -px
      pz = -pz
    }
    return CGSize(width: px * 16 * scale, height: -pz * 16 * scale)
  }

  func svg() -> String {
    let scale = 100.0
    let padding = 48.0
    let drawingWidth = max((maxX - minX) * scale + padding * 2, 220)
    let drawingHeight = max((maxZ - minZ) * scale + padding * 2, 220)
    let legend = legendLines()
    let height = drawingHeight + 28 + 18 * Double(legend.count)
    func mapped(_ point: Point) -> (Double, Double) {
      (
        (point.x - minX) * scale + padding,
        (maxZ - point.z) * scale + padding
      )
    }
    let wallMarkup = walls.map { wall -> String in
      let start = mapped(wall.start)
      let end = mapped(wall.end)
      return String(
        format: "<line x1=\"%.2f\" y1=\"%.2f\" x2=\"%.2f\" y2=\"%.2f\" stroke=\"%@\" data-wall=\"%d\" />",
        start.0, start.1, end.0, end.1, wall.colorHex, wall.index
      )
    }
    let openingMarkup = openings.map { opening -> String in
      let start = mapped(opening.start)
      let end = mapped(opening.end)
      return String(
        format: "<line x1=\"%.2f\" y1=\"%.2f\" x2=\"%.2f\" y2=\"%.2f\" stroke=\"%@\" data-%@=\"%d\" />",
        start.0, start.1, end.0, end.1, opening.colorHex, opening.kind.rawValue, opening.index
      )
    }
    let labels = walls.map { wall -> String in
      let mid = mapped(wall.midpoint)
      let offset = labelOffset(for: wall)
      return String(
        format: "<text x=\"%.2f\" y=\"%.2f\" fill=\"%@\" text-anchor=\"middle\" dominant-baseline=\"middle\">%d</text>",
        mid.0 + offset.width, mid.1 + offset.height, wall.colorHex, wall.index
      )
    }
    let compassX = drawingWidth - 28
    let legendMarkup = legend.enumerated().map { index, line in
      let y = drawingHeight + 18 + Double(index) * 18
      return "<text x=\"\(Int(padding))\" y=\"\(Int(y))\" fill=\"\(line.0)\">\(Self.escape(line.1))</text>"
    }
    let rooms = roomLabels.map(Self.escape).joined(separator: ", ")
    return """
    <?xml version="1.0" encoding="UTF-8"?>
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 \(String(format: "%.2f", drawingWidth)) \(String(format: "%.2f", height))" role="img" aria-labelledby="title desc">
      <title id="title">Tagged RoomPlan floor plan</title>
      <desc id="desc">Numbered walls and openings for \(rooms). N is scan +Z, not magnetic north.</desc>
      <rect width="100%" height="100%" fill="#1C1C1E" />
      <g stroke-width="8" stroke-linecap="square">
        \(wallMarkup.joined(separator: "\n        "))
      </g>
      <g stroke-width="10" stroke-linecap="butt">
        \(openingMarkup.joined(separator: "\n        "))
      </g>
      <g font-family="-apple-system, Helvetica, sans-serif" font-size="14" font-weight="700">
        \(labels.joined(separator: "\n        "))
      </g>
      <g transform="translate(\(String(format: "%.1f", compassX)),32)" fill="#E8E8ED" font-family="-apple-system, Helvetica, sans-serif" font-size="13" font-weight="700" text-anchor="middle">
        <line x1="0" y1="10" x2="0" y2="-12" stroke="#E8E8ED" stroke-width="2" />
        <polygon points="0,-16 -4,-8 4,-8" fill="#E8E8ED" />
        <text x="0" y="-20">N</text>
      </g>
      <g font-family="-apple-system, Helvetica, sans-serif" font-size="12">
        \(legendMarkup.joined(separator: "\n        "))
      </g>
    </svg>
    """
  }

  func legendLines() -> [(String, String)] {
    var lines = [("#9A9AA2", "\(summaryLine). N is scan +Z, not magnetic north."), ("#E8E8ED", "Walls")]
    for wall in walls {
      lines.append((wall.colorHex, "Wall \(wall.index)  \(wall.lengthCm) cm · \(wall.compass)"))
    }
    let groups: [(Opening.Kind, String)] = [(.door, "Doors"), (.window, "Windows"), (.opening, "Openings")]
    for (kind, title) in groups {
      let items = openings.filter { $0.kind == kind }
      guard !items.isEmpty else { continue }
      lines.append(("#E8E8ED", title))
      for opening in items {
        let parent = opening.wallIndex.map { " · on wall \($0)" } ?? ""
        lines.append((opening.colorHex, "\(title.dropLast()) \(opening.index)  \(opening.lengthCm) cm\(parent)"))
      }
    }
    return lines
  }

  private static func openingColor(_ kind: Opening.Kind) -> String {
    switch kind {
    case .door: "#F4F4F5"
    case .window: "#A8D8FF"
    case .opening: "#D0D0D4"
    }
  }

  private static func segment(_ surface: PortableSurface) throws -> (Point, Point) {
    guard surface.dimensions.count == 3, surface.transform.count == 16 else {
      throw PackageWriterError.invalidGeometry("A surface has incomplete dimensions or transform.")
    }
    let centerX = Double(surface.transform[12])
    let centerZ = Double(surface.transform[14])
    var axisX = Double(surface.transform[0])
    var axisZ = Double(surface.transform[2])
    let magnitude = (axisX * axisX + axisZ * axisZ).squareRoot()
    guard magnitude > 0.000_001 else {
      throw PackageWriterError.invalidGeometry("A surface has a degenerate transform.")
    }
    axisX /= magnitude
    axisZ /= magnitude
    let half = Double(surface.dimensions[0]) / 2
    return (
      Point(x: centerX - axisX * half, z: centerZ - axisZ * half),
      Point(x: centerX + axisX * half, z: centerZ + axisZ * half)
    )
  }

  private static func outwardCompass(_ wall: Wall, centroid: Point) -> String {
    var px = -(wall.end.z - wall.start.z)
    var pz = wall.end.x - wall.start.x
    let length = (px * px + pz * pz).squareRoot()
    if length > 0 {
      px /= length
      pz /= length
    }
    let mid = wall.midpoint
    if (mid.x - centroid.x) * px + (mid.z - centroid.z) * pz < 0 {
      px = -px
      pz = -pz
    }
    return compass((px, pz))
  }

  private static func compass(_ normal: (Double, Double)) -> String {
    if abs(normal.1) >= abs(normal.0) {
      return normal.1 > 0 ? "N" : "S"
    }
    return normal.0 > 0 ? "E" : "W"
  }

  private static func parentWallIndex(
    _ surface: PortableSurface,
    start: Point,
    end: Point,
    walls: [Wall]
  ) -> Int? {
    if let parent = surface.parentIdentifier {
      if let match = walls.first(where: { $0.identifier.caseInsensitiveCompare(parent) == .orderedSame }) {
        return match.index
      }
    }
    let mid = Point(x: (start.x + end.x) / 2, z: (start.z + end.z) / 2)
    var bestIndex: Int?
    var bestDistance = 0.35
    for wall in walls {
      let distance = pointSegmentDistance(mid, start: wall.start, end: wall.end)
      if distance < bestDistance {
        bestDistance = distance
        bestIndex = wall.index
      }
    }
    return bestIndex
  }

  private static func pointSegmentDistance(_ point: Point, start: Point, end: Point) -> Double {
    let dx = end.x - start.x
    let dz = end.z - start.z
    let lengthSq = dx * dx + dz * dz
    if lengthSq < 1e-12 {
      return ((point.x - start.x) * (point.x - start.x) + (point.z - start.z) * (point.z - start.z)).squareRoot()
    }
    let t = min(1, max(0, ((point.x - start.x) * dx + (point.z - start.z) * dz) / lengthSq))
    let px = start.x + t * dx
    let pz = start.z + t * dz
    return ((point.x - px) * (point.x - px) + (point.z - pz) * (point.z - pz)).squareRoot()
  }

  private static func floorArea(
    walls: [Wall],
    minX: Double,
    maxX: Double,
    minZ: Double,
    maxZ: Double
  ) -> Double {
    if let polygon = orderedPolygon(walls), polygon.count >= 3 {
      var area = 0.0
      for index in polygon.indices {
        let next = polygon[(index + 1) % polygon.count]
        area += polygon[index].x * next.z - next.x * polygon[index].z
      }
      area = abs(area) / 2
      if area > 0.05 { return area }
    }
    return (maxX - minX) * (maxZ - minZ)
  }

  private static func orderedPolygon(_ walls: [Wall]) -> [Point]? {
    guard let first = walls.first else { return nil }
    let snap = 0.08
    var unused = Array(walls.dropFirst())
    var points = [first.start, first.end]
    while !unused.isEmpty {
      let current = points[points.count - 1]
      var matchIndex: Int?
      var matchPoint: Point?
      for (index, wall) in unused.enumerated() {
        if hypot(wall.start.x - current.x, wall.start.z - current.z) <= snap {
          matchIndex = index
          matchPoint = wall.end
          break
        }
        if hypot(wall.end.x - current.x, wall.end.z - current.z) <= snap {
          matchIndex = index
          matchPoint = wall.start
          break
        }
      }
      guard let matchIndex, let matchPoint else { return nil }
      points.append(matchPoint)
      unused.remove(at: matchIndex)
    }
    guard hypot(points[0].x - points[points.count - 1].x, points[0].z - points[points.count - 1].z) <= snap else {
      return nil
    }
    return Array(points.dropLast())
  }

  private static func escape(_ value: String) -> String {
    value
      .replacingOccurrences(of: "&", with: "&amp;")
      .replacingOccurrences(of: "<", with: "&lt;")
      .replacingOccurrences(of: ">", with: "&gt;")
  }
}

extension Color {
  init(floorPlanHex hex: String) {
    let trimmed = hex.trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
    var value: UInt64 = 0
    Scanner(string: trimmed).scanHexInt64(&value)
    self.init(
      red: Double((value >> 16) & 0xFF) / 255,
      green: Double((value >> 8) & 0xFF) / 255,
      blue: Double(value & 0xFF) / 255
    )
  }
}
