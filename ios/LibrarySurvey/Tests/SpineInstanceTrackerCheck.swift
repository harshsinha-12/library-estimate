import CoreGraphics
import Foundation

@main
struct SpineInstanceTrackerCheck {
  static func main() {
    var tracker = SpineInstanceTracker()
    func observation(_ x: Double, _ y: Double, stacked: Bool = false) -> SpineFaceObservation {
      SpineFaceObservation(
        faceX: x, faceY: y,
        widthMeters: stacked ? 0.12 : 0.018,
        heightMeters: stacked ? 0.025 : 0.22,
        box: CGRect(
          x: x, y: y,
          width: stacked ? 0.12 : 0.02,
          height: stacked ? 0.025 : 0.2
        ),
        hasReadableText: true,
        isStacked: stacked, isLeaning: false,
        time: 1, jpeg: Data("fixture".utf8)
      )
    }
    let save: (UUID, CGRect, Data) -> String? = { id, _, _ in "crop/\(id).jpg" }
    tracker.ingest(
      rowId: "row_01", observations: [observation(0.300, 0.2), observation(0.325, 0.2)],
      saveCrop: save
    )
    let firstIds = Set((tracker.instances["row_01"] ?? []).map(\.id))
    precondition(firstIds.count == 2)
    tracker.ingest(
      rowId: "row_01", observations: [observation(0.326, 0.2), observation(0.301, 0.2)],
      saveCrop: save
    )
    precondition(Set((tracker.instances["row_01"] ?? []).map(\.id)) == firstIds)
    tracker.ingest(
      rowId: "row_01",
      observations: [
        SpineFaceObservation(
          faceX: 0.301, faceY: 0.2,
          widthMeters: 0.02, heightMeters: 0.22,
          box: CGRect(x: 0.298, y: 0.195, width: 0.028, height: 0.21),
          hasReadableText: true, isStacked: false, isLeaning: false,
          time: 2, jpeg: Data("fixture".utf8)
        )
      ],
      saveCrop: save
    )
    precondition(Set((tracker.instances["row_01"] ?? []).map(\.id)) == firstIds)
    tracker.ingest(
      rowId: "row_01",
      observations: [observation(0.500, 0.26, stacked: true)],
      saveCrop: save
    )
    precondition((tracker.instances["row_01"] ?? []).count == 3)
    tracker.ingest(
      rowId: "row_01",
      observations: [
        SpineFaceObservation(
          faceX: 0.55, faceY: 0.4,
          widthMeters: 0.12, heightMeters: 0.03,
          box: CGRect(x: 0.5, y: 0.4, width: 0.12, height: 0.03),
          hasReadableText: false, isStacked: true, isLeaning: false,
          time: 3, jpeg: Data("fixture".utf8)
        ),
        SpineFaceObservation(
          faceX: 0.12, faceY: 0.72,
          widthMeters: 0.04, heightMeters: 0.18,
          box: CGRect(x: 0.10, y: 0.70, width: 0.05, height: 0.16),
          hasReadableText: false, isStacked: false, isLeaning: false,
          time: 3, jpeg: Data("fixture".utf8)
        )
      ],
      saveCrop: save
    )
    precondition((tracker.instances["row_01"] ?? []).count == 3)
    precondition((tracker.instances["row_01"] ?? []).allSatisfy { !$0.evidenceRef.isEmpty })
    let spines = tracker.labeledSpines(rowId: "row_01")
    let observationIds = Set(spines.compactMap { $0["observation_id"] as? String })
    precondition(observationIds.count == 3)
    precondition(spines.count == observationIds.count)
    print("spine tracker fixture passed")
  }
}
