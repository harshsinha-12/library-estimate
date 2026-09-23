import CoreImage
import Foundation
import UIKit
import Vision

struct SpineRegion {
  let box: CGRect
  let hasReadableText: Bool
  let confidence: Float
  let isStacked: Bool
  let isLeaning: Bool
  let label: String
}

enum LiveQualityAnalyzer {
  static func spineRegions(jpeg: Data) -> [CGRect] {
    spineCandidates(jpeg: jpeg).map(\.box)
  }

  static func overlayCandidates(jpeg: Data) -> [SpineRegion] {
    proposals(jpeg: jpeg, mintUnread: true)
  }

  static func spineCandidates(jpeg: Data) -> [SpineRegion] {
    // Copies still gate on OCR: if !readable { return nil }
    overlayCandidates(jpeg: jpeg).filter(\.hasReadableText)
  }

  private static func proposals(jpeg: Data, mintUnread: Bool) -> [SpineRegion] {
    guard let image = UIImage(data: jpeg)?.cgImage else { return [] }
    let request = VNDetectRectanglesRequest()
    request.minimumAspectRatio = 0.035
    request.maximumAspectRatio = 1.2
    request.minimumSize = 0.04
    request.minimumConfidence = 0.5
    request.maximumObservations = 100
    let textRequest = VNRecognizeTextRequest()
    textRequest.recognitionLevel = .fast
    textRequest.minimumTextHeight = 0.01
    try? VNImageRequestHandler(cgImage: image, options: [:]).perform([request, textRequest])
    let texts: [(box: CGRect, string: String)] = (textRequest.results ?? []).compactMap { observation in
      guard let candidate = observation.topCandidates(1).first else { return nil }
      guard candidate.string.filter(\.isLetter).count >= 3 else { return nil }
      return (observation.boundingBox, candidate.string)
    }
    let textBoxes = texts.map(\.box)
    let proposals: [SpineRegion] = (request.results ?? []).compactMap { observation in
      let box = observation.boundingBox
      let tall = box.height > box.width * 1.4
      let stacked = box.width > box.height * 1.4
      let narrow = tall ? box.width <= 0.25 : box.height <= 0.45
      let minSpan = stacked ? 0.12 : 0.07
      let minArea = stacked ? 0.02 : 0.008
      guard (tall || stacked), narrow, box.width * box.height <= 0.55,
            max(box.width, box.height) >= minSpan,
            box.width * box.height >= minArea else { return nil }
      let top = observation.topLeft
      let bottom = observation.bottomLeft
      let lean = tall && abs(top.x - bottom.x) > box.width * 0.4
      let readable = textBoxes.contains { Self.boxesOverlap($0, box) }
      // Crochet / table squares and nested inner cover boxes have no unique
      // title letters. Do not mint those as copies; unread stays partial.
      if !readable { if !mintUnread { return nil } }
      let title = texts.first { Self.boxesOverlap($0.box, box) }?.string
      let score = String(format: "%.2f", observation.confidence)
      let caption = title.map { "\($0) \(score)" } ?? "book \(score)"
      return SpineRegion(
        box: box, hasReadableText: readable, confidence: observation.confidence,
        isStacked: stacked, isLeaning: lean, label: caption
      )
    }
    var selected: [SpineRegion] = []
    for proposal in proposals.sorted(by: {
      $0.box.width * $0.box.height > $1.box.width * $1.box.height
    }) {
      if selected.contains(where: { Self.boxesOverlap($0.box, proposal.box) }) { continue }
      selected.append(proposal)
    }
    return selected
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

  static func cropContainsText(jpeg: Data, box: CGRect) -> Bool {
    guard let image = UIImage(data: jpeg)?.cgImage else { return false }
    return cropContainsText(image: image, box: box)
  }

  static func cropContainsText(image: CGImage, box: CGRect) -> Bool {
    let rect = CGRect(
      x: box.minX * CGFloat(image.width),
      y: (1 - box.maxY) * CGFloat(image.height),
      width: max(1, box.width * CGFloat(image.width)),
      height: max(1, box.height * CGFloat(image.height))
    ).integral
    guard let crop = image.cropping(to: rect) else { return false }
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .fast
    request.minimumTextHeight = 0.08
    try? VNImageRequestHandler(cgImage: crop, options: [:]).perform([request])
    let letters = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }
      .joined()
      .filter(\.isLetter)
    return letters.count >= 3
  }

  static func analyze(
    jpeg: Data,
    previousTransform: [Float]?,
    currentTransform: [Float],
    dt: Double,
    provisionalCount: Int
  ) -> LiveQualityReading {
    guard let image = UIImage(data: jpeg)?.cgImage else { return .idle }
    let blur = laplacianVariance(image)
    let glare = highlightFraction(image)
    let speed = motion(previous: previousTransform, current: currentTransform, dt: dt)
    let text = averageTextHeight(image)
    // Pixel count is not an occlusion measurement. Missing spines remain a partial row
    // until an independently confirmed actual count reconciles with the candidates.
    let occlusion = 0.0
    var messages: [String] = []
    let blurScore = max(0, min(1, 1 - blur / 180))
    if blurScore > 0.55 { messages.append("Hold still — the frame is blurry") }
    if glare > 0.35 { messages.append("Tilt to reduce glare") }
    if speed > 0.45 { messages.append("Slow down the sweep") }
    if text > 0 && text < 14 { messages.append("Move closer — spine text is too small") }
    return LiveQualityReading(
      blur: blurScore,
      glare: glare,
      speed: speed,
      textPixelHeight: text,
      occlusion: occlusion,
      messages: messages,
      provisionalCount: provisionalCount
    )
  }

  private static func laplacianVariance(_ image: CGImage) -> Double {
    let ci = CIImage(cgImage: image)
    let filter = CIFilter(name: "CIConvolution3X3")
    filter?.setValue(ci, forKey: kCIInputImageKey)
    filter?.setValue(
      CIVector(values: [0, 1, 0, 1, -4, 1, 0, 1, 0], count: 9),
      forKey: "inputWeights"
    )
    guard let output = filter?.outputImage else { return 80 }
    let context = CIContext(options: [.useSoftwareRenderer: false])
    var bitmap = [UInt8](repeating: 0, count: 4)
    context.render(
      output,
      toBitmap: &bitmap,
      rowBytes: 4,
      bounds: CGRect(x: 0, y: 0, width: 1, height: 1),
      format: .RGBA8,
      colorSpace: CGColorSpaceCreateDeviceRGB()
    )
    return Double(bitmap[0])
  }

  private static func highlightFraction(_ image: CGImage) -> Double {
    guard let data = image.dataProvider?.data else { return 0 }
    let pointer = CFDataGetBytePtr(data)
    let length = CFDataGetLength(data)
    guard let pointer, length > 0 else { return 0 }
    var hot = 0
    var samples = 0
    var index = 0
    while index + 2 < length {
      let r = Int(pointer[index])
      let g = Int(pointer[index + 1])
      let b = Int(pointer[index + 2])
      if r > 245 && g > 245 && b > 245 { hot += 1 }
      samples += 1
      index += 64
    }
    return samples == 0 ? 0 : Double(hot) / Double(samples)
  }

  private static func motion(previous: [Float]?, current: [Float], dt: Double) -> Double {
    guard let previous, previous.count >= 16, current.count >= 16, dt > 0 else { return 0 }
    let dx = Double(current[12] - previous[12])
    let dy = Double(current[13] - previous[13])
    let dz = Double(current[14] - previous[14])
    return min(1, sqrt(dx * dx + dy * dy + dz * dz) / max(dt, 0.01) / 1.2)
  }

  private static func averageTextHeight(_ image: CGImage) -> Double {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .fast
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    try? handler.perform([request])
    let heights = (request.results ?? []).compactMap { observation -> Double? in
      guard observation.topCandidates(1).first != nil else { return nil }
      return Double(observation.boundingBox.height) * Double(image.height)
    }
    guard !heights.isEmpty else { return 24 }
    return heights.reduce(0, +) / Double(heights.count)
  }

}
