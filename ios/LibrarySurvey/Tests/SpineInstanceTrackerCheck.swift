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
        box: CGRect(x: x, y: y, width: 0.02, height: 0.2),
        hasReadableText: false,
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
      rowId: "row_01", observations: [observation(0.300, 0.26, stacked: true)],
      saveCrop: save
    )
    precondition((tracker.instances["row_01"] ?? []).count == 3)
    precondition((tracker.instances["row_01"] ?? []).allSatisfy { !$0.evidenceRef.isEmpty })
    print("spine tracker fixture passed")
  }
}
