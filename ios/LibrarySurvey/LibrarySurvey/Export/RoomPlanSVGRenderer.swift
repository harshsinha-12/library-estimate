import Foundation

enum RoomPlanSVGRenderer {
  static func render(_ structure: PortableStructure) throws -> String {
    try FloorPlanLayout(structure).svg()
  }
}
