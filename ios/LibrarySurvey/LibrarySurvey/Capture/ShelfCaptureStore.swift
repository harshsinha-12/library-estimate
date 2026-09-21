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

  private var session: ARSession?
  private let sampler = FrameSampler()
  private var previousTransform: [Float]?
  private var previousTime: Double?

  var selectedUnit: ShelfUnit? {
    units.first { $0.id == selectedUnitId } ?? units.first
  }

  func attach(session: ARSession) {
    self.session = session
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
    previousTransform = nil
    capturing = true
    sampler.start(session: session ?? ARSession(), interval: 0.4)
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
    previousTransform = frame.cameraTransform
    previousTime = now
    updateCoverage()
  }

  func stopFace() {
    sampler.stop(captureFallback: true)
    samples = sampler.samples
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

  func combinedLabeledPackage() -> LabeledShelfPackage {
    var passes: [LabeledPass] = []
    for capture in captures {
      if let decoded = try? JSONCoding.decoder().decode(LabeledShelfPackage.self, from: capture.labeled) {
        passes.append(contentsOf: decoded.passes)
      }
    }
    return LabeledShelfPackage(passes: passes)
  }

  private func updateCoverage() {
    guard !rowCoverage.isEmpty else { return }
    let usable = quality.messages.isEmpty
    let increment = usable ? 0.18 : 0.04
    let index = min(rowCoverage.count - 1, max(0, Int(Double(samples.count) / 6.0)))
    for offset in 0...index {
      rowCoverage[offset].coverage = min(1, rowCoverage[offset].coverage + increment)
      rowCoverage[offset].copyCount = max(rowCoverage[offset].copyCount, assistCount / max(1, rowCoverage.count - offset))
      rowCoverage[offset].status = rowCoverage[offset].coverage >= 0.8 ? "ok" : "partial"
    }
    recaptureRows = rowCoverage.filter { $0.status != "ok" }.map(\.rowId)
  }

  private func labeledPass(unit: ShelfUnit, face: ShelfFaceSide, samples: [FrameSample]) -> LabeledPass {
    let faceId = face == .a ? unit.faceAId : unit.faceBId
    let normal: [Double] = face == .a ? [0, 0, 1] : [0, 0, -1]
    return LabeledPass(
      passId: "\(faceId)-\(Int(Date().timeIntervalSince1970))",
      roomId: unit.roomName.lowercased(),
      shelfId: unit.slug,
      faceId: faceId,
      faceNormal: normal,
      label: "\(unit.name) \(face.rawValue)",
      minX: 0.4,
      minZ: face == .a ? 0.3 : -0.2,
      maxX: 1.6,
      maxZ: face == .a ? 0.7 : 0.2,
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
      rows: rowCoverage.enumerated().map { index, row in
        let spines: [LabeledSpine] = (0..<max(0, row.copyCount)).map { slot in
          LabeledSpine(
            slot: slot,
            x: 0.1 + Double(slot) * 0.12,
            t: samples.last?.monotonicSeconds ?? 0,
            isbn: nil,
            appearance: "live-\(slot)",
            evidenceRef: "shelf_frame_\(index)_\(slot)"
          )
        }
        return LabeledRow(
          rowId: row.rowId,
          coverage: row.coverage,
          capacityM: 0.4,
          spines: spines
        )
      }
    )
  }
}
