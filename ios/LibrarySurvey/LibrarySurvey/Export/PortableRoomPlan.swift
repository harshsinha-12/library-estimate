import RoomPlan
import simd

struct PortableStructure: Codable {
  let format: String
  let rooms: [PortableRoom]
}

struct PortableRoom: Codable {
  let identifier: String
  let label: String
  let walls: [PortableSurface]
  let doors: [PortableSurface]
  let windows: [PortableSurface]
  let openings: [PortableSurface]
}

struct PortableSurface: Codable {
  let identifier: String
  let dimensions: [Float]
  let transform: [Float]
  let confidence: String
  let parentIdentifier: String?
}

enum RoomPlanAdapter {
  static func portableStructure(
    from rooms: [CapturedRoom],
    defaultLabel: String
  ) async throws -> PortableStructure {
    let aligned: [CapturedRoom]
    if rooms.count > 1 {
      aligned = try await StructureBuilder(options: []).capturedStructure(from: rooms).rooms
    } else {
      aligned = rooms
    }
    return PortableStructure(
      format: AppConfiguration.roomPlanFormat,
      rooms: aligned.enumerated().map { index, room in
        portableRoom(
          room,
          label: index == 0 ? defaultLabel : "\(defaultLabel) \(index + 1)"
        )
      }
    )
  }

  private static func portableRoom(_ room: CapturedRoom, label: String) -> PortableRoom {
    PortableRoom(
      identifier: room.identifier.uuidString,
      label: label,
      walls: room.walls.map(portableSurface),
      doors: room.doors.map(portableSurface),
      windows: room.windows.map(portableSurface),
      openings: room.openings.map(portableSurface)
    )
  }

  private static func portableSurface(_ surface: CapturedRoom.Surface) -> PortableSurface {
    let matrix = surface.transform
    let transform = [matrix.columns.0, matrix.columns.1, matrix.columns.2, matrix.columns.3]
      .flatMap { [$0.x, $0.y, $0.z, $0.w] }
    let confidence: String = switch surface.confidence {
    case .high: "high"
    case .medium: "medium"
    case .low: "low"
    @unknown default: "unknown"
    }
    return PortableSurface(
      identifier: surface.identifier.uuidString,
      dimensions: [surface.dimensions.x, surface.dimensions.y, surface.dimensions.z],
      transform: transform,
      confidence: confidence,
      parentIdentifier: surface.parentIdentifier?.uuidString
    )
  }
}
