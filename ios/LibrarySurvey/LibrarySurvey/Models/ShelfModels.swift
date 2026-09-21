import Foundation

enum ShelfFaceSide: String, Codable, CaseIterable, Identifiable {
  case a = "A"
  case b = "B"
  var id: String { rawValue }
}

struct ShelfUnit: Identifiable, Codable, Equatable {
  let id: UUID
  var name: String
  var rowCount: Int
  var roomName: String

  var faceAId: String { "\(slug).face_A" }
  var faceBId: String { "\(slug).face_B" }
  var slug: String {
    name.lowercased().replacingOccurrences(of: " ", with: "_")
  }
}

struct ShelfRowCoverage: Identifiable, Codable, Equatable {
  var id: String { rowId }
  let rowId: String
  var coverage: Double
  var status: String
  var copyCount: Int
}

struct LiveQualityReading: Equatable {
  var blur: Double
  var glare: Double
  var speed: Double
  var textPixelHeight: Double
  var occlusion: Double
  var messages: [String]
  var provisionalCount: Int

  static let idle = LiveQualityReading(
    blur: 0,
    glare: 0,
    speed: 0,
    textPixelHeight: 24,
    occlusion: 0,
    messages: [],
    provisionalCount: 0
  )
}

struct ShelfFaceCapture: Identifiable, Codable, Equatable {
  let id: UUID
  let unitId: UUID
  let face: ShelfFaceSide
  let faceId: String
  var rows: [ShelfRowCoverage]
  var frames: Int
  var evidenceBytes: Int
  var labeled: Data
}

struct LabeledShelfPackage: Codable {
  var passes: [LabeledPass]
}

struct LabeledPass: Codable {
  var passId: String
  var roomId: String
  var shelfId: String
  var faceId: String
  var faceNormal: [Double]
  var label: String
  var minX: Double
  var minZ: Double
  var maxX: Double
  var maxZ: Double
  var capacityM: Double
  var evidenceBytes: Int
  var t: Double
  var quality: LabeledQuality
  var rows: [LabeledRow]
}

struct LabeledQuality: Codable {
  var blur: Double
  var glare: Double
  var speed: Double
  var textPixelHeight: Double
  var occlusion: Double
}

struct LabeledRow: Codable {
  var rowId: String
  var coverage: Double
  var capacityM: Double
  var spines: [LabeledSpine]
}

struct LabeledSpine: Codable {
  var slot: Int
  var x: Double
  var t: Double
  var isbn: String?
  var appearance: String
  var evidenceRef: String
}
