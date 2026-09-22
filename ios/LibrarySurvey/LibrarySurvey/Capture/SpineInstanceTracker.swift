import CoreGraphics
import Foundation

struct SpineFaceObservation {
  let faceX: Double
  let faceY: Double
  let widthMeters: Double
  let heightMeters: Double
  let box: CGRect
  let hasReadableText: Bool
  let isStacked: Bool
  let isLeaning: Bool
  let time: Double
  let jpeg: Data
}

struct SpineInstance: Identifiable {
  let id: UUID
  let rowId: String
  var faceX: Double
  var faceY: Double
  var widthMeters: Double
  var heightMeters: Double
  var box: CGRect
  var lastSeen: Double
  var observations: Int
  var hasReadableText: Bool
  var isStacked: Bool
  var isLeaning: Bool
  var evidenceRef: String
}

struct SpineInstanceTracker {
  private(set) var instances: [String: [SpineInstance]] = [:]
  private(set) var uncertainRows: Set<String> = []

  mutating func reset() {
    instances = [:]
    uncertainRows = []
  }

  mutating func markUncertain(_ rowId: String) {
    uncertainRows.insert(rowId)
  }

  func labeledSpines(rowId: String) -> [[String: Any]] {
    (instances[rowId] ?? []).enumerated().map { slot, spine in
      [
        "slot": slot,
        "x": spine.faceX,
        "t": spine.lastSeen,
        "appearance": spine.id.uuidString,
        "observation_id": spine.id.uuidString,
        "evidence_ref": spine.evidenceRef,
        "readable": spine.hasReadableText,
        "stacked": spine.isStacked,
        "leaning": spine.isLeaning,
      ]
    }
  }

  mutating func ingest(
    rowId: String,
    observations: [SpineFaceObservation],
    saveCrop: (UUID, CGRect, Data) -> String?
  ) {
    var row = instances[rowId] ?? []
    var used: Set<UUID> = []
    for observation in observations.sorted(by: { $0.faceX < $1.faceX }) {
      let matches = row.enumerated().compactMap { index, candidate -> (Int, Double)? in
        let distanceX = abs(candidate.faceX - observation.faceX)
        let distanceY = abs(candidate.faceY - observation.faceY)
        let span = max(candidate.widthMeters, observation.widthMeters)
        let tall = max(candidate.heightMeters, observation.heightMeters)
        let limitX = max(0.018, min(0.04, span * 0.9))
        let limitY = max(0.03, min(0.08, tall * 0.35))
        let sameStack = candidate.isStacked == observation.isStacked
        let closeEnough = distanceX + distanceY < 0.015
        let overlap = Self.boxesOverlap(candidate.box, observation.box)
        guard overlap || (distanceX <= limitX && distanceY <= limitY && (sameStack || closeEnough))
        else { return nil }
        return (index, overlap ? min(distanceX + distanceY, 0.01) : distanceX + distanceY)
      }.sorted { $0.1 < $1.1 }
      if matches.count > 1 {
        uncertainRows.insert(rowId)
      }
      if let match = matches.first, !used.contains(row[match.0].id) {
        let index = match.0
        used.insert(row[index].id)
        row[index].faceX = (row[index].faceX * 3 + observation.faceX) / 4
        row[index].faceY = (row[index].faceY * 3 + observation.faceY) / 4
        row[index].widthMeters = (row[index].widthMeters * 3 + observation.widthMeters) / 4
        row[index].heightMeters = (row[index].heightMeters * 3 + observation.heightMeters) / 4
        row[index].lastSeen = observation.time
        row[index].observations += 1
        row[index].hasReadableText = row[index].hasReadableText || observation.hasReadableText
        row[index].isLeaning = row[index].isLeaning || observation.isLeaning
        row[index].isStacked = row[index].isStacked || observation.isStacked
        row[index].box = observation.box
        if let path = saveCrop(row[index].id, observation.box, observation.jpeg) {
          row[index].evidenceRef = path
        }
      } else if matches.isEmpty {
        if !observation.hasReadableText {
          uncertainRows.insert(rowId)
          continue
        }
        let id = UUID()
        let path = saveCrop(id, observation.box, observation.jpeg) ?? ""
        if path.isEmpty { uncertainRows.insert(rowId) }
        row.append(SpineInstance(
          id: id, rowId: rowId, faceX: observation.faceX, faceY: observation.faceY,
          widthMeters: observation.widthMeters, heightMeters: observation.heightMeters,
          box: observation.box,
          lastSeen: observation.time, observations: 1,
          hasReadableText: observation.hasReadableText,
          isStacked: observation.isStacked, isLeaning: observation.isLeaning,
          evidenceRef: path
        ))
        used.insert(id)
      } else {
        uncertainRows.insert(rowId)
      }
    }
    instances[rowId] = row.sorted {
      abs($0.faceX - $1.faceX) < 0.005 ? $0.faceY < $1.faceY : $0.faceX < $1.faceX
    }
  }

  static func boxesOverlap(_ a: CGRect, _ b: CGRect) -> Bool {
    let overlap = a.intersection(b)
    guard !overlap.isNull, overlap.width > 0, overlap.height > 0 else { return false }
    let smaller = min(a.width * a.height, b.width * b.height)
    if smaller > 0 && overlap.width * overlap.height / smaller >= 0.35 { return true }
    if a.contains(CGPoint(x: b.midX, y: b.midY)) || b.contains(CGPoint(x: a.midX, y: a.midY)) {
      return true
    }
    return abs(a.midX - b.midX) < max(0.024, min(a.width, b.width) * 0.5)
      && overlap.height > min(a.height, b.height) * 0.3
  }
}
