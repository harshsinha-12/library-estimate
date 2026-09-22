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
  @Published var imageSize: CGSize = .zero
  private var tracker = SpineInstanceTracker()
  private var facePlane: simd_float4x4?
  private var coveredBins: [String: Set<Int>] = [:]
  private var lastProcessedFrameId: UUID?
  private var faceWidthMeters = 1.2
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
    visibleSpines = []
    assistCount = 0
    quality = .idle
    rowCoverage = []
    recaptureRows = []
    samples = []
    tracker.reset()
    facePlane = nil
    coveredBins = [:]
    lastProcessedFrameId = nil
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
    visibleSpines = []
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
    lastProcessedFrameId = nil
    activeRowId = rowCoverage[0].rowId
    faceWidthMeters = max(0.35, unit.footprint(for: face).maxX - unit.footprint(for: face).minX)
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

  func selectRow(_ rowId: String) {
    guard rowCoverage.contains(where: { $0.rowId == rowId }) else { return }
    activeRowId = rowId
    highlightedSpines = []
    visibleSpines = []
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
    taggedCrops[instance.evidenceRef].flatMap(UIImage.init(data:))
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
    guard frame.id != lastProcessedFrameId else { return }
    lastProcessedFrameId = frame.id
    let now = frame.monotonicSeconds
    let dt = previousTime.map { now - $0 } ?? 0.4
    let detected = LiveQualityAnalyzer.spineCandidates(jpeg: frame.jpegData).filter {
      (0.28...0.72).contains($0.box.midY)
    }
    quality = LiveQualityAnalyzer.analyze(
      jpeg: frame.jpegData,
      previousTransform: previousTransform,
      currentTransform: frame.cameraTransform,
      dt: dt,
      provisionalCount: detected.count
    )
    assistCount = quality.provisionalCount
    highlightedSpines = detected.map(\.box)
    if let image = UIImage(data: frame.jpegData) { imageSize = image.size }
    mergeSpines(detected, sample: frame)
    previousTransform = frame.cameraTransform
    previousTime = now
    updateCoverage(detected: detected, sample: frame)
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

  private func mergeSpines(_ detected: [SpineRegion], sample: FrameSample) {
    guard let index = rowCoverage.firstIndex(where: { $0.rowId == activeRowId }),
          let projection = sampler.projections[sample.id], imageSize.width > 0 else { return }
    if facePlane == nil { facePlane = makeFacePlane(camera: projection.camera) }
    let observations = detected.compactMap { region -> SpineFaceObservation? in
      guard let center = project(region.box.midX, region.box.midY,
                                 camera: projection.camera, orientation: projection.orientation),
            let left = project(region.box.minX, region.box.midY,
                               camera: projection.camera, orientation: projection.orientation),
            let right = project(region.box.maxX, region.box.midY,
                                camera: projection.camera, orientation: projection.orientation),
            let top = project(region.box.midX, region.box.maxY,
                              camera: projection.camera, orientation: projection.orientation),
            let bottom = project(region.box.midX, region.box.minY,
                                 camera: projection.camera, orientation: projection.orientation)
      else { return nil }
      let width = abs(right.x - left.x)
      let height = abs(top.y - bottom.y)
      guard width >= 0.003, width <= (region.isStacked ? 0.6 : 0.2),
            height >= 0.003, height <= 0.8 else { return nil }
      return SpineFaceObservation(
        faceX: center.x, faceY: center.y,
        widthMeters: width, heightMeters: height, box: region.box,
        hasReadableText: region.hasReadableText,
        isStacked: region.isStacked, isLeaning: region.isLeaning,
        time: sample.monotonicSeconds, jpeg: sample.jpegData
      )
    }
    if observations.count < detected.count { tracker.markUncertain(activeRowId) }
    tracker.ingest(rowId: activeRowId, observations: observations) { [weak self] id, box, jpeg in
      guard let self, let crop = self.crop(jpeg, box: box) else { return nil }
      let path = "shelf_scans/crops/\(self.activeRowId)_\(id.uuidString).jpg"
      self.taggedCrops[path] = crop
      return path
    }
    let row = tracker.instances[activeRowId] ?? []
    rowCoverage[index].copyCount = row.count
    visibleSpines = observations.compactMap { observation in
      guard let closest = row.enumerated().min(by: {
        abs($0.element.faceX - observation.faceX) + abs($0.element.faceY - observation.faceY) <
        abs($1.element.faceX - observation.faceX) + abs($1.element.faceY - observation.faceY)
      }), abs(closest.element.faceX - observation.faceX) <= 0.03,
            abs(closest.element.faceY - observation.faceY) <= 0.05 else { return nil }
      return ShelfVisibleSpine(
        id: closest.element.id, box: observation.box, rowId: activeRowId,
        slot: closest.offset, hasReadableText: closest.element.hasReadableText
      )
    }
    updateRowStatus(index)
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

  private func updateCoverage(detected: [SpineRegion], sample: FrameSample) {
    guard let index = rowCoverage.firstIndex(where: { $0.rowId == activeRowId }),
          let projection = sampler.projections[sample.id],
          quality.messages.isEmpty,
          detected.count > 0,
          detected.filter(\.hasReadableText).count * 2 >= detected.count,
          let left = project(0.08, 0.5, camera: projection.camera,
                             orientation: projection.orientation),
          let right = project(0.92, 0.5, camera: projection.camera,
                              orientation: projection.orientation)
    else { return }
    let lower = Int(floor(min(left.x, right.x) / 0.08))
    let upper = Int(floor(max(left.x, right.x) / 0.08))
    guard upper >= lower, upper - lower <= 30 else { return }
    coveredBins[activeRowId, default: []].formUnion(lower...upper)
    rowCoverage[index].coverage = min(
      1, Double(coveredBins[activeRowId, default: []].count) * 0.08 / faceWidthMeters
    )
    updateRowStatus(index)
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
      trackingMode: "ar_local_plane_assumed_0.65m"
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
