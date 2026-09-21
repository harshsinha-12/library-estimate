import Foundation
import SwiftUI
import UIKit
import Vision

@MainActor
final class ExceptionCaptureStore: ObservableObject {
  @Published var marks: [OtherAssetMark] = []
  @Published var scans: [ExceptionScan] = []
  @Published var notes: [ExceptionNote] = []
  @Published var focusEvents: [FocusEvent] = []
  @Published var latestBarcode = ""
  @Published var latestText = ""
  @Published var latestConfidence: Double = 0
  @Published var latestImageRef: String?
  @Published var latestImage: UIImage?
  @Published var candidateRegions: [CGRect] = []
  @Published var suggestedCategory: AssetCategory?
  @Published var errorMessage: String?
  @Published var focusedFaceId: String?
  @Published var focusedRow: Int = 1
  @Published var focusedSlot: Int = 0
  var currentCameraPose: [Float]?
  var pointedAt: Double?

  func pointAtShelf(faceId: String, row: Int, slot: Int) {
    let moment = MonotonicClock.now
    pointedAt = moment
    focusEvents.append(FocusEvent(id: UUID().uuidString.lowercased(),
                                  monotonicSeconds: moment, assetCopyId: nil,
                                  faceId: faceId, rowId: String(format: "row_%02d", row),
                                  slot: slot, cameraPose: currentCameraPose))
  }

  func pointAtOther() { pointedAt = MonotonicClock.now }
  var images: [String: Data] = [:]

  func capture(_ image: UIImage) {
    guard let cgImage = image.cgImage, let jpeg = image.jpegData(compressionQuality: 0.88) else {
      errorMessage = "Could not read captured image."
      return
    }
    let path = "closeups/\(UUID().uuidString.lowercased()).jpg"
    images[path] = jpeg
    latestImageRef = path
    latestImage = image
    do {
      let barcodes = VNDetectBarcodesRequest()
      let text = VNRecognizeTextRequest()
      let rectangles = VNDetectRectanglesRequest()
      rectangles.maximumObservations = 30
      let classifier = VNClassifyImageRequest()
      text.recognitionLevel = .accurate
      try VNImageRequestHandler(cgImage: cgImage).perform([barcodes, text, rectangles, classifier])
      latestBarcode = barcodes.results?.compactMap(\.payloadStringValue).first ?? ""
      let recognized = text.results?.compactMap { $0.topCandidates(1).first } ?? []
      latestText = recognized.map(\.string).joined(separator: "\n")
      latestConfidence = Double(recognized.first?.confidence ?? 0)
      candidateRegions = (rectangles.results ?? []).map(\.boundingBox)
      let label = classifier.results?.first?.identifier.lowercased() ?? ""
      if label.contains("book") { suggestedCategory = .book }
      else if label.contains("portrait") || label.contains("picture frame") { suggestedCategory = .portrait }
      else if label.contains("cup") || label.contains("mug") { suggestedCategory = .cup }
      else if label.contains("monitor") { suggestedCategory = .monitor }
      else if label.contains("computer") { suggestedCategory = .computer }
      else if label.contains("furniture") { suggestedCategory = .furniture }
      else { suggestedCategory = nil }
      errorMessage = nil
    } catch {
      errorMessage = "Vision could not read this image; enter the visible text manually."
    }
  }

  func focus(on region: CGRect) {
    guard let image = latestImage?.cgImage else { return }
    let x = max(0, region.minX - 0.04)
    let y = max(0, region.minY - 0.04)
    let width = min(1 - x, region.width + 0.08)
    let height = min(1 - y, region.height + 0.08)
    let crop = CGRect(x: x * CGFloat(image.width), y: (1 - y - height) * CGFloat(image.height),
                      width: width * CGFloat(image.width), height: height * CGFloat(image.height)).integral
    if let cropped = image.cropping(to: crop) { capture(UIImage(cgImage: cropped)) }
  }

  func addMark(category: AssetCategory, label: String, room: String, highValue: Bool) {
    let id = UUID().uuidString.lowercased()
    marks.append(OtherAssetMark(
      id: id, assetCopyId: "asset_\(id)", category: category.rawValue,
      label: label, roomId: room, monotonicSeconds: pointedAt ?? MonotonicClock.now,
      evidenceRef: latestImageRef ?? "operator_mark", highValue: highValue,
      cameraPose: currentCameraPose
    ))
    focusEvents.append(FocusEvent(id: UUID().uuidString.lowercased(),
                                  monotonicSeconds: pointedAt ?? MonotonicClock.now,
                                  assetCopyId: "asset_\(id)", faceId: nil, rowId: nil, slot: nil,
                                  cameraPose: currentCameraPose))
    pointedAt = nil
  }

  func addScan(kind: String, assetId: String?, faceId: String?, rowId: String?, slot: Int?,
               identifierKind: String, title: String, scope: String,
               damageType: String, severity: String, region: String, closeupRef: String?, scaleRef: String?) {
    let evidence = latestImageRef ?? "operator_entry"
    scans.append(ExceptionScan(
      id: UUID().uuidString.lowercased(), kind: kind, assetCopyId: assetId,
      faceId: faceId, rowId: rowId, slot: slot, cameraPose: currentCameraPose,
      monotonicSeconds: MonotonicClock.now, evidenceRef: evidence,
      barcode: latestBarcode.isEmpty ? nil : latestBarcode,
      identifierKind: identifierKind, title: title.isEmpty ? nil : title,
      ocrText: latestText.isEmpty ? nil : latestText, ocrConfidence: latestConfidence,
      scope: scope, damageType: kind == "damage" ? damageType : nil,
      severity: kind == "damage" ? severity : nil, region: kind == "damage" ? region : nil,
      closeupRef: kind == "damage" ? closeupRef : nil,
      scaleRef: kind == "damage" ? scaleRef : nil
    ))
  }

  func addNote(text: String, assetId: String?, category: AssetCategory?,
               faceId: String?, rowId: String?, slot: Int?) {
    notes.append(ExceptionNote(
      id: UUID().uuidString.lowercased(), text: text,
      monotonicSeconds: MonotonicClock.now, tappedAssetId: assetId,
      reticleAssetId: assetId, poseAssetId: nil,
      categoryHints: category.map { [$0.rawValue] } ?? [],
      faceId: faceId, rowId: rowId, slot: slot, cameraPose: currentCameraPose
    ))
  }

  func reset() {
    marks = []
    scans = []
    notes = []
    focusEvents = []
    images = [:]
    latestBarcode = ""
    latestText = ""
    latestImageRef = nil
    latestImage = nil
    candidateRegions = []
    suggestedCategory = nil
    focusedFaceId = nil
    currentCameraPose = nil
    pointedAt = nil
  }
}
