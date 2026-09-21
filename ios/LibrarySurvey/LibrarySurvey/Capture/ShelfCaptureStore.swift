import ARKit
import Foundation
import RealityKit
import SwiftUI

@MainActor
final class ShelfCaptureStore: ObservableObject {
  @Published var units: [ShelfUnit] = [
    ShelfUnit(id: UUID(), name: "Shelf 1", rowCount: 3, roomName: "Library")
  ]
  @Published var selectedUnitId: UUID?
  @Published var selectedFace: ShelfFaceSide = .a
  @Published var capturing = false
  @Published var quality = LiveQualityReading.idle
  @Published var rowCoverage: [ShelfRowCoverage] = []
  @Published var recaptureRows: [String] = []
  @Published var captures: [ShelfFaceCapture] = []
  @Published var samples: [FrameSample] = []
  @Published var assistCount = 0
  @Published var highlightedSpines: [CGRect] = []
  @Published var imageSize: CGSize = .zero
  private var rowSpines: [String: [AccumulatedSpine]] = [:]
  private var taggedCrops: [String: Data] = [:]

  private var session: ARSession?
  private let sampler = FrameSampler()
  private var previousTransform: [Float]?
  private var previousTime: Double?
  private let camera: CameraSessionCoordinator

  init(camera: CameraSessionCoordinator) {
    self.camera = camera
  }

  var selectedUnit: ShelfUnit? {
    units.first { $0.id == selectedUnitId } ?? units.first
  }

  var footprintPlacementSummary: String {
    units.contains { $0.footprint?.isOperatorPlaced == true }
      ? "operator_placed"
      : "unregistered_overlay"
  }

  func attach(session: ARSession) {
    self.session = session
  }

  func detachSession() {
    sampler.stop(captureFallback: false)
    session = nil
    camera.release(.shelfAR)
  }

  func releaseCamera() {
    capturing = false
    sampler.stop(captureFallback: false)
    session?.pause()
    session = nil
    camera.release(.shelfAR)
  }

  func setFootprint(unitId: UUID, minX: Double, minZ: Double, maxX: Double, maxZ: Double) {
    guard let index = units.firstIndex(where: { $0.id == unitId }) else { return }
    let left = min(minX, maxX)
    let right = max(minX, maxX)
    let near = min(minZ, maxZ)
    let far = max(minZ, maxZ)
    let width = max(0.35, right - left)
    let depth = max(0.25, far - near)
    units[index].footprint = ShelfFootprint(
      minX: left,
      minZ: near,
      maxX: left + width,
      maxZ: near + depth,
      placement: ShelfFootprint.operatorKind
    )
  }

  func clearFootprint(unitId: UUID) {
    guard let index = units.firstIndex(where: { $0.id == unitId }) else { return }
    units[index].footprint = nil
  }

  func clearLiveAssist() {
    capturing = false
    highlightedSpines = []
    assistCount = 0
    quality = .idle
    rowCoverage = []
    recaptureRows = []
    samples = []
    rowSpines = [:]
    taggedCrops = [:]
    previousTransform = nil
    previousTime = nil
  }

  func addUnit() {
    units.append(
      ShelfUnit(
        id: UUID(),
        name: "Shelf \(units.count + 1)",
        rowCount: 3,
        roomName: "Library"
      )
    )
  }

  func startFace(unit: ShelfUnit, face: ShelfFaceSide) {
    selectedUnitId = unit.id
    selectedFace = face
    highlightedSpines = []
    assistCount = 0
    rowCoverage = (1...max(1, unit.rowCount)).map { index in
      ShelfRowCoverage(
        rowId: String(format: "row_%02d", index),
        coverage: 0,
        status: "partial",
        copyCount: 0
      )
    }
    recaptureRows = rowCoverage.map(\.rowId)
    samples = []
    rowSpines = [:]
    previousTransform = nil
    taggedCrops = [:]
    guard let session else {
      capturing = false
      return
    }
    capturing = true
    sampler.start(session: session, interval: 0.4)
  }

  func pauseFace() {
    guard capturing else { return }
    sampler.stop(captureFallback: false)
    samples.append(contentsOf: sampler.samples)
    capturing = false
  }

  func resumeFace() {
    guard !capturing, !rowCoverage.isEmpty, let session else { return }
    capturing = true
    previousTransform = nil
    previousTime = nil
    sampler.start(session: session, interval: 0.4)
  }

  func ingestCurrentFrame() {
    guard capturing, let frame = sampler.samples.last else { return }
    let now = frame.monotonicSeconds
    let dt = previousTime.map { now - $0 } ?? 0.4
    quality = LiveQualityAnalyzer.analyze(
      jpeg: frame.jpegData,
      previousTransform: previousTransform,
      currentTransform: frame.cameraTransform,
      dt: dt
    )
    assistCount = quality.provisionalCount
    highlightedSpines = LiveQualityAnalyzer.spineRegions(jpeg: frame.jpegData)
    if let image = UIImage(data: frame.jpegData) { imageSize = image.size }
    mergeSpines(highlightedSpines, jpeg: frame.jpegData, time: now, frameIndex: sampler.samples.count)
    previousTransform = frame.cameraTransform
    previousTime = now
    updateCoverage()
  }

  func stopFace() {
    if capturing {
      sampler.stop(captureFallback: true)
      samples.append(contentsOf: sampler.samples)
    }
    capturing = false
    guard let unit = selectedUnit else { return }
    let labeled = labeledPass(unit: unit, face: selectedFace, samples: samples)
    let data = (try? JSONCoding.encoder().encode(LabeledShelfPackage(passes: [labeled]))) ?? Data()
    captures.removeAll { $0.faceId == labeled.faceId }
    captures.append(
      ShelfFaceCapture(
        id: UUID(),
        unitId: unit.id,
        face: selectedFace,
        faceId: labeled.faceId,
        rows: rowCoverage,
        frames: samples.count,
        evidenceBytes: samples.reduce(0) { $0 + $1.jpegData.count },
        labeled: data
      )
    )
  }

  func focusedImage(for region: CGRect) -> UIImage? {
    guard let data = sampler.samples.last?.jpegData,
          let image = UIImage(data: data)?.cgImage else { return nil }
    let padding: CGFloat = 0.05
    let expanded = CGRect(x: max(0, region.minX - padding), y: max(0, region.minY - padding),
                          width: min(1 - max(0, region.minX - padding), region.width + padding * 2),
                          height: min(1 - max(0, region.minY - padding), region.height + padding * 2))
    let crop = CGRect(x: expanded.minX * CGFloat(image.width),
                      y: (1 - expanded.maxY) * CGFloat(image.height),
                      width: expanded.width * CGFloat(image.width),
                      height: expanded.height * CGFloat(image.height)).integral
    guard let cropped = image.cropping(to: crop) else { return nil }
    return UIImage(cgImage: cropped)
  }

  func currentImage() -> UIImage? {
    guard let data = sampler.samples.last?.jpegData else { return nil }
    return UIImage(data: data)
  }

  func currentJpeg() -> Data? { sampler.samples.last?.jpegData }

  func taggedEvidence() -> [String: Data] { taggedCrops }

  func displayRect(for box: CGRect, in viewSize: CGSize) -> CGRect {
    guard imageSize.width > 1, imageSize.height > 1, viewSize.width > 1, viewSize.height > 1 else {
      return CGRect(
        x: box.minX * viewSize.width,
        y: (1 - box.maxY) * viewSize.height,
        width: box.width * viewSize.width,
        height: box.height * viewSize.height
      )
    }
    let scale = max(viewSize.width / imageSize.width, viewSize.height / imageSize.height)
    let scaled = CGSize(width: imageSize.width * scale, height: imageSize.height * scale)
    let origin = CGPoint(
      x: (viewSize.width - scaled.width) / 2,
      y: (viewSize.height - scaled.height) / 2
    )
    return CGRect(
      x: origin.x + box.minX * scaled.width,
      y: origin.y + (1 - box.maxY) * scaled.height,
      width: box.width * scaled.width,
      height: box.height * scaled.height
    )
  }

  func currentPose() -> [Float]? { sampler.samples.last?.cameraTransform }

  func combinedLabeledPackage() -> LabeledShelfPackage {
    var passes: [LabeledPass] = []
    for capture in captures {
      if let decoded = try? JSONCoding.decoder().decode(LabeledShelfPackage.self, from: capture.labeled) {
        passes.append(contentsOf: decoded.passes)
      }
    }
    return LabeledShelfPackage(passes: passes)
  }

  private func mergeSpines(_ boxes: [CGRect], jpeg: Data, time: Double, frameIndex: Int) {
    guard !rowCoverage.isEmpty else { return }
    for box in boxes {
      let rowIndex = min(
        rowCoverage.count - 1,
        max(0, Int((1 - box.midY) * CGFloat(rowCoverage.count)))
      )
      let rowId = rowCoverage[rowIndex].rowId
      let x = Double(box.midX)
      var list = rowSpines[rowId] ?? []
      if let match = list.enumerated().min(by: { abs($0.element.x - x) < abs($1.element.x - x) }),
         abs(match.element.x - x) <= 0.06 {
        list[match.offset].x = (list[match.offset].x + x) / 2
        list[match.offset].t = time
        list[match.offset].box = box
      } else if LiveQualityAnalyzer.cropContainsText(jpeg: jpeg, box: box) {
        list.append(
          AccumulatedSpine(
            slot: list.count,
            x: x,
            t: time,
            evidenceRef: "shelf_frame_\(frameIndex)",
            box: box
          )
        )
        list.sort { $0.x < $1.x }
        for index in list.indices { list[index].slot = index }
      }
      for index in list.indices {
        let path = "shelf_scans/crops/\(rowId)_slot\(list[index].slot).jpg"
        if let crop = crop(jpeg, box: list[index].box) {
          taggedCrops[path] = crop
          list[index].evidenceRef = path
        }
      }
      rowSpines[rowId] = list
      rowCoverage[rowIndex].copyCount = list.count
    }
  }

  private func crop(_ jpeg: Data, box: CGRect) -> Data? {
    guard let image = UIImage(data: jpeg)?.cgImage else { return nil }
    let rect = CGRect(
      x: box.minX * CGFloat(image.width),
      y: (1 - box.maxY) * CGFloat(image.height),
      width: max(1, box.width * CGFloat(image.width)),
      height: max(1, box.height * CGFloat(image.height))
    ).integral
    guard let cropped = image.cropping(to: rect) else { return nil }
    return UIImage(cgImage: cropped).jpegData(compressionQuality: 0.82)
  }

  private func updateCoverage() {
    guard !rowCoverage.isEmpty else { return }
    let usable = quality.messages.isEmpty
    let increment = usable ? 0.18 : 0.04
    let index = min(rowCoverage.count - 1, max(0, Int(Double(sampler.samples.count) / 6.0)))
    for offset in 0...index {
      rowCoverage[offset].coverage = min(1, rowCoverage[offset].coverage + increment)
      rowCoverage[offset].status = rowCoverage[offset].coverage >= 0.8 ? "ok" : "partial"
    }
    recaptureRows = rowCoverage.filter { $0.status != "ok" }.map(\.rowId)
  }

  private func labeledPass(unit: ShelfUnit, face: ShelfFaceSide, samples: [FrameSample]) -> LabeledPass {
    let faceId = face == .a ? unit.faceAId : unit.faceBId
    let normal: [Double] = face == .a ? [0, 0, 1] : [0, 0, -1]
    let box = unit.footprint(for: face)
    return LabeledPass(
      passId: "\(faceId)-\(Int(Date().timeIntervalSince1970))",
      roomId: unit.roomName.lowercased(),
      shelfId: unit.slug,
      faceId: faceId,
      faceNormal: normal,
      label: "\(unit.name) \(face.rawValue)",
      minX: box.minX,
      minZ: box.minZ,
      maxX: box.maxX,
      maxZ: box.maxZ,
      capacityM: Double(unit.rowCount) * 0.4,
      evidenceBytes: samples.reduce(0) { $0 + $1.jpegData.count },
      t: samples.last?.monotonicSeconds ?? 0,
      quality: LabeledQuality(
        blur: quality.blur,
        glare: quality.glare,
        speed: quality.speed,
        textPixelHeight: quality.textPixelHeight,
        occlusion: quality.occlusion
      ),
      rows: rowCoverage.map { row in
        let stored = (rowSpines[row.rowId] ?? []).sorted { $0.x < $1.x }
        let spines: [LabeledSpine] = stored.enumerated().map { slot, spine in
          LabeledSpine(
            slot: slot,
            x: spine.x,
            t: spine.t,
            isbn: nil,
            appearance: "live-\(slot)",
            evidenceRef: spine.evidenceRef
          )
        }
        return LabeledRow(
          rowId: row.rowId,
          coverage: row.coverage,
          capacityM: 0.4,
          actualCount: row.actualCount,
          spines: spines
        )
      },
      placement: box.placement
    )
  }
}

private struct AccumulatedSpine {
  var slot: Int
  var x: Double
  var t: Double
  var evidenceRef: String
  var box: CGRect
}
