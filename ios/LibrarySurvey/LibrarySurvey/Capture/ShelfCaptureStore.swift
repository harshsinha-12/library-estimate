import ARKit
import Foundation
import RealityKit
import simd
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
  @Published var activeRowId = "row_01"
  @Published var visibleSpines: [ShelfVisibleSpine] = []
  @Published var frameOverlays: [ShelfOverlayBox] = []
  @Published var overlaySource = "vision"
  @Published var imageSize: CGSize = .zero
  private var tracker = SpineInstanceTracker()
  private var facePlane: simd_float4x4?
  private var coveredBins: [String: Set<Int>] = [:]
  private var rowBands: [String: (min: Double, max: Double)] = [:]
  private var lastProcessedFrameId: UUID?
  private var faceWidthMeters = 1.2
  private var taggedCrops: [String: Data] = [:]
  private var taggedFrames: [String: Data] = [:]

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
    if !capturing {
      sampler.start(session: session, interval: 0.4)
    }
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
    visibleSpines = []
    frameOverlays = []
    overlaySource = "vision"
    assistCount = 0
    quality = .idle
    rowCoverage = []
    recaptureRows = []
    samples = []
    tracker.reset()
    facePlane = nil
    coveredBins = [:]
    rowBands = [:]
    lastProcessedFrameId = nil
    taggedCrops = [:]
    taggedFrames = [:]
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
    visibleSpines = []
    overlaySource = overlaySource == "yolo" ? "yolo" : "vision"
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
    tracker.reset()
    facePlane = nil
    coveredBins = [:]
    rowBands = [:]
    lastProcessedFrameId = nil
    activeRowId = rowCoverage[0].rowId
    faceWidthMeters = max(0.35, unit.footprint(for: face).maxX - unit.footprint(for: face).minX)
    previousTransform = nil
    taggedCrops = [:]
    taggedFrames = [:]
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

  func selectRow(_ rowId: String) {
    guard rowCoverage.contains(where: { $0.rowId == rowId }) else { return }
    activeRowId = rowId
    if capturing { rebuildVisibleSpines() }
  }

  func confirmActualCount(for rowId: String) {
    guard let index = rowCoverage.firstIndex(where: { $0.rowId == rowId }) else { return }
    rowCoverage[index].actualCount = rowCoverage[index].copyCount
    updateRowStatus(index)
  }

  func setActualCount(_ count: Int, for rowId: String) {
    guard let index = rowCoverage.firstIndex(where: { $0.rowId == rowId }) else { return }
    rowCoverage[index].actualCount = count
    updateRowStatus(index)
  }

  func instances(for rowId: String) -> [SpineInstance] {
    tracker.instances[rowId] ?? []
  }

  func evidenceImage(for instance: SpineInstance) -> UIImage? {
    taggedFrames[instance.evidenceRef].flatMap(UIImage.init(data:))
      ?? taggedCrops[instance.evidenceRef].flatMap(UIImage.init(data:))
  }

  func resumeFace() {
    guard !capturing, !rowCoverage.isEmpty, let session else { return }
    capturing = true
    previousTransform = nil
    previousTime = nil
    sampler.start(session: session, interval: 0.4)
  }

  func ingestCurrentFrame() {
    guard let frame = sampler.samples.last else { return }
    guard frame.id != lastProcessedFrameId else { return }
    lastProcessedFrameId = frame.id
    let overlays = LiveQualityAnalyzer.overlayCandidates(jpeg: frame.jpegData)
    if let image = UIImage(data: frame.jpegData) { imageSize = image.size }
    if overlaySource != "yolo" {
      applyVisionOverlays(overlays)
    }
    guard capturing else { return }
    let now = frame.monotonicSeconds
    let dt = previousTime.map { now - $0 } ?? 0.4
    let detected = LiveQualityAnalyzer.spineCandidates(jpeg: frame.jpegData)
    quality = LiveQualityAnalyzer.analyze(
      jpeg: frame.jpegData,
      previousTransform: previousTransform,
      currentTransform: frame.cameraTransform,
      dt: dt,
      provisionalCount: detected.count
    )
    mergeSpines(detected, sample: frame)
    assistCount = rowCoverage.reduce(0) { $0 + $1.copyCount }
    highlightedSpines = visibleSpines.map(\.box)
    previousTransform = frame.cameraTransform
    previousTime = now
  }

  func applyVisionOverlays(_ regions: [SpineRegion]) {
    overlaySource = "vision"
    frameOverlays = regions.enumerated().map { index, region in
      ShelfOverlayBox(
        id: "vision-\(index)",
        box: region.box,
        caption: region.label,
        source: "vision",
        readable: region.hasReadableText
      )
    }
  }

  func applyYoloOverlays(_ boxes: [ShelfOverlayBox]) {
    guard !boxes.isEmpty else { return }
    overlaySource = "yolo"
    frameOverlays = boxes
  }

  func stopFace() {
    if capturing {
      sampler.stop(captureFallback: true)
      samples.append(contentsOf: sampler.samples)
    }
    capturing = false
    if let session {
      sampler.start(session: session, interval: 0.4)
    }
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

  func focusedImage(for _: CGRect) -> UIImage? {
    currentImage()
  }

  func currentImage() -> UIImage? {
    guard let data = sampler.samples.last?.jpegData else { return nil }
    return UIImage(data: data)
  }

  func currentJpeg() -> Data? { sampler.samples.last?.jpegData }

  func taggedEvidence() -> [String: Data] { taggedCrops }

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

  private func mergeSpines(_ detected: [SpineRegion], sample: FrameSample) {
    let projection = sampler.projections[sample.id]
    if let projection, facePlane == nil {
      facePlane = makeFacePlane(camera: projection.camera)
    }
    var byRow: [String: [SpineFaceObservation]] = [:]
    for region in detected {
      let point = facePoint(region.box, stacked: region.isStacked, projection: projection)
      guard let rowId = assignRow(faceY: point.y) else { continue }
      byRow[rowId, default: []].append(
        SpineFaceObservation(
          faceX: point.x, faceY: point.y,
          widthMeters: point.width, heightMeters: point.height, box: region.box,
          hasReadableText: region.hasReadableText,
          isStacked: region.isStacked, isLeaning: region.isLeaning,
          time: sample.monotonicSeconds, jpeg: sample.jpegData
        )
      )
    }
    for (rowId, observations) in byRow {
      tracker.ingest(rowId: rowId, observations: observations) { [weak self] id, box, jpeg in
        guard let self else { return nil }
        let path = "shelf_scans/crops/\(rowId)_\(id.uuidString).jpg"
        if let crop = self.crop(jpeg, box: box) {
          self.taggedCrops[path] = crop
        }
        self.taggedFrames[path] = jpeg
        return path
      }
      expandBand(rowId, faceYs: observations.filter(\.hasReadableText).map(\.faceY))
      updateCoverage(rowId: rowId, observations: observations)
    }
    for index in rowCoverage.indices {
      rowCoverage[index].copyCount = (tracker.instances[rowCoverage[index].rowId] ?? []).count
      updateRowStatus(index)
    }
    rebuildVisibleSpines()
  }

  private func rebuildVisibleSpines() {
    visibleSpines = rowCoverage.flatMap { row in
      (tracker.instances[row.rowId] ?? []).enumerated().map { slot, spine in
        ShelfVisibleSpine(
          id: spine.id, box: spine.box, rowId: row.rowId,
          slot: slot, hasReadableText: spine.hasReadableText
        )
      }
    }
  }

  private func updateCoverage(rowId: String, observations: [SpineFaceObservation]) {
    guard let index = rowCoverage.firstIndex(where: { $0.rowId == rowId }) else { return }
    let readable = observations.filter(\.hasReadableText)
    guard !readable.isEmpty else { return }
    let xs = readable.map(\.faceX)
    let lower = Int(floor((xs.min() ?? 0) / 0.08))
    let upper = Int(floor((xs.max() ?? 0) / 0.08))
    guard upper >= lower, upper - lower <= 30 else { return }
    coveredBins[rowId, default: []].formUnion(lower...upper)
    rowCoverage[index].coverage = min(
      1, Double(coveredBins[rowId, default: []].count) * 0.08 / faceWidthMeters
    )
  }

  private func facePoint(
    _ box: CGRect,
    stacked: Bool,
    projection: (camera: ARCamera, orientation: UIInterfaceOrientation)?
  ) -> (x: Double, y: Double, width: Double, height: Double) {
    if let projection,
       let center = project(box.midX, box.midY, camera: projection.camera,
                            orientation: projection.orientation),
       let left = project(box.minX, box.midY, camera: projection.camera,
                          orientation: projection.orientation),
       let right = project(box.maxX, box.midY, camera: projection.camera,
                           orientation: projection.orientation),
       let top = project(box.midX, box.maxY, camera: projection.camera,
                         orientation: projection.orientation),
       let bottom = project(box.midX, box.minY, camera: projection.camera,
                            orientation: projection.orientation)
    {
      let width = abs(right.x - left.x)
      let height = abs(top.y - bottom.y)
      if width >= 0.003, width <= (stacked ? 0.8 : 0.25),
         height >= 0.003, height <= 0.9
      {
        return (center.x, center.y, width, height)
      }
    }
    return (
      Double(box.midX) * faceWidthMeters,
      Double(box.midY),
      max(0.01, Double(box.width) * faceWidthMeters),
      max(0.01, Double(box.height))
    )
  }

  private func assignRow(faceY: Double) -> String? {
    for row in rowCoverage {
      if let band = rowBands[row.rowId],
         faceY >= band.min - 0.05,
         faceY <= band.max + 0.05
      {
        return row.rowId
      }
    }
    let count = max(1, rowCoverage.count)
    if count > 1, let minY = rowBands.values.map({ $0.min }).min(),
       let maxY = rowBands.values.map({ $0.max }).max(), maxY - minY > 0.08
    {
      let t = (faceY - minY) / (maxY - minY)
      let index = min(count - 1, max(0, Int((1 - t) * Double(count))))
      return rowCoverage[index].rowId
    }
    // First readable detections bootstrap the selected row. Later texture
    // boxes above/below that band must not dump onto row_01.
    if rowBands[activeRowId] == nil { return activeRowId }
    return nil
  }

  private func expandBand(_ rowId: String, faceYs: [Double]) {
    guard let low = faceYs.min(), let high = faceYs.max() else { return }
    if let band = rowBands[rowId] {
      rowBands[rowId] = (min(band.min, low), max(band.max, high))
    } else {
      rowBands[rowId] = (low, high)
    }
  }

  private func updateRowStatus(_ index: Int) {
    let row = rowCoverage[index]
    let reconciled = row.actualCount == row.copyCount
    rowCoverage[index].status = row.coverage >= 0.8 && reconciled &&
      !tracker.uncertainRows.contains(row.rowId) ? "ok" : "partial"
    recaptureRows = rowCoverage.filter { $0.status != "ok" }.map(\.rowId)
  }

  private func makeFacePlane(camera: ARCamera) -> simd_float4x4? {
    let transform = camera.transform
    let rawRight = SIMD3<Float>(transform.columns.0.x, 0, transform.columns.0.z)
    guard simd_length(rawRight) > 0.1 else { return nil }
    let right = simd_normalize(rawRight)
    let normal = simd_cross(SIMD3<Float>(0, 1, 0), right)
    let position = SIMD3<Float>(transform.columns.3.x, transform.columns.3.y,
                                transform.columns.3.z) + normal * 0.65
    return simd_float4x4(
      SIMD4<Float>(right.x, right.y, right.z, 0),
      SIMD4<Float>(normal.x, normal.y, normal.z, 0),
      SIMD4<Float>(0, 1, 0, 0),
      SIMD4<Float>(position.x, position.y, position.z, 1)
    )
  }

  private func project(_ x: CGFloat, _ y: CGFloat, camera: ARCamera,
                       orientation: UIInterfaceOrientation) -> SIMD2<Double>? {
    guard let facePlane, imageSize.width > 0, imageSize.height > 0 else { return nil }
    let viewportPoint = CGPoint(x: x * imageSize.width, y: (1 - y) * imageSize.height)
    guard let world = camera.unprojectPoint(
      viewportPoint, ontoPlane: facePlane, orientation: orientation,
      viewportSize: imageSize
    ) else { return nil }
    let local = simd_inverse(facePlane) * SIMD4<Float>(world.x, world.y, world.z, 1)
    guard local.x.isFinite, local.z.isFinite,
          abs(local.x) < 4, abs(local.z) < 4 else { return nil }
    return SIMD2<Double>(Double(local.x), Double(local.z))
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
        let stored = tracker.instances[row.rowId] ?? []
        let spines: [LabeledSpine] = stored.enumerated().map { slot, spine in
          LabeledSpine(
            slot: slot,
            x: spine.faceX / faceWidthMeters,
            t: spine.lastSeen,
            isbn: nil,
            appearance: spine.id.uuidString,
            evidenceRef: spine.evidenceRef,
            observationId: spine.id.uuidString,
            readable: spine.hasReadableText,
            stacked: spine.isStacked,
            leaning: spine.isLeaning
          )
        }
        return LabeledRow(
          rowId: row.rowId,
          coverage: row.coverage,
          capacityM: 0.4,
          actualCount: row.actualCount,
          spines: spines,
          captureStatus: row.status
        )
      },
      placement: box.placement,
      trackingMode: "shelf_face_xy_persistent;ar_plane_or_image_fallback"
    )
  }
}

struct ShelfVisibleSpine: Identifiable {
  let id: UUID
  var box: CGRect
  let rowId: String
  let slot: Int
  let hasReadableText: Bool
}

struct ShelfOverlayBox: Identifiable {
  let id: String
  let box: CGRect
  let caption: String
  let source: String
  let readable: Bool
}
