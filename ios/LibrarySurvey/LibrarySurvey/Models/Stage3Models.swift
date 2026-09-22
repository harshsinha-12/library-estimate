import Foundation

enum AssetCategory: String, CaseIterable, Codable, Identifiable {
  case book, serial, painting, portrait, sculpture, computer, monitor, printer
  case furniture, shelf, appliance, cup, decorativeObject = "decorative_object", other
  var id: String { rawValue }
  var label: String { rawValue.replacingOccurrences(of: "_", with: " ").capitalized }
}

struct OtherAssetMark: Codable, Identifiable {
  let id: String
  let assetCopyId: String
  let category: String
  let label: String
  let roomId: String
  let monotonicSeconds: Double
  let evidenceRef: String
  let highValue: Bool
  let cameraPose: [Float]?
  let statedCost: Double?
  let statedCurrency: String?
}

struct ExceptionScan: Codable, Identifiable {
  let id: String
  let kind: String
  let assetCopyId: String?
  let faceId: String?
  let rowId: String?
  let slot: Int?
  let cameraPose: [Float]?
  let monotonicSeconds: Double
  let evidenceRef: String
  let barcode: String?
  let identifierKind: String?
  let title: String?
  let ocrText: String?
  let ocrConfidence: Double?
  let scope: String?
  let format: String?
  let damageType: String?
  let severity: String?
  let region: String?
  let closeupRef: String?
  let scaleRef: String?
}

struct ExceptionNote: Codable, Identifiable {
  let id: String
  let text: String
  let monotonicSeconds: Double
  let tappedAssetId: String?
  let reticleAssetId: String?
  let poseAssetId: String?
  let categoryHints: [String]
  let faceId: String?
  let rowId: String?
  let slot: Int?
  let cameraPose: [Float]?
}

struct PassCPackage: Codable {
  let scans: [ExceptionScan]
  let notes: [ExceptionNote]
  let focusEvents: [FocusEvent]
}

struct FocusEvent: Codable, Identifiable {
  let id: String
  let monotonicSeconds: Double
  let assetCopyId: String?
  let faceId: String?
  let rowId: String?
  let slot: Int?
  let cameraPose: [Float]?
}
