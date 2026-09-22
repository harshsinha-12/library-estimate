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
        let limitX = max(0.012, min(0.025, min(candidate.widthMeters, observation.widthMeters) * 0.42))
        let limitY = max(0.02, min(0.05, min(candidate.heightMeters, observation.heightMeters) * 0.2))
        guard distanceX <= limitX, distanceY <= limitY,
              candidate.isStacked == observation.isStacked else { return nil }
        return (index, distanceX + distanceY)
      }.sorted { $0.1 < $1.1 }
      if matches.count > 1 {
        uncertainRows.insert(rowId)
        continue
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
        if observation.hasReadableText || row[index].observations == 2 {
          row[index].box = observation.box
          if let path = saveCrop(row[index].id, observation.box, observation.jpeg) {
            row[index].evidenceRef = path
          }
        }
      } else if matches.isEmpty {
        let id = UUID()
        guard let path = saveCrop(id, observation.box, observation.jpeg) else {
          uncertainRows.insert(rowId)
          continue
        }
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
}
