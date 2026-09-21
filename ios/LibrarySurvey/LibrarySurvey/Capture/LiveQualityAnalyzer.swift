import CoreImage
import Foundation
import UIKit
import Vision

enum LiveQualityAnalyzer {
  static func spineRegions(jpeg: Data) -> [CGRect] {
    guard let image = UIImage(data: jpeg)?.cgImage else { return [] }
    let request = VNDetectRectanglesRequest()
    request.minimumAspectRatio = 0.08
    request.maximumAspectRatio = 0.65
    request.minimumSize = 0.02
    request.maximumObservations = 80
    try? VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
    return (request.results ?? [])
      .filter { $0.confidence >= 0.5 && $0.boundingBox.height > $0.boundingBox.width * 1.25 }
      .map(\.boundingBox)
  }

  static func analyze(
    jpeg: Data,
    previousTransform: [Float]?,
    currentTransform: [Float],
    dt: Double
  ) -> LiveQualityReading {
    guard let image = UIImage(data: jpeg)?.cgImage else { return .idle }
    let blur = laplacianVariance(image)
    let glare = highlightFraction(image)
    let speed = motion(previous: previousTransform, current: currentTransform, dt: dt)
    let text = averageTextHeight(image)
    let occlusion = 1 - min(1, Double(image.width * image.height) / 4_000_000)
    var messages: [String] = []
    let blurScore = max(0, min(1, 1 - blur / 180))
    if blurScore > 0.55 { messages.append("Hold still — the frame is blurry") }
    if glare > 0.35 { messages.append("Tilt to reduce glare") }
    if speed > 0.45 { messages.append("Slow down the sweep") }
    if text > 0 && text < 14 { messages.append("Move closer — spine text is too small") }
    if occlusion > 0.55 { messages.append("Shelf face is occluded") }
    return LiveQualityReading(
      blur: blurScore,
      glare: glare,
      speed: speed,
      textPixelHeight: text,
      occlusion: occlusion,
      messages: messages,
      provisionalCount: spineRegions(jpeg: jpeg).count
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
