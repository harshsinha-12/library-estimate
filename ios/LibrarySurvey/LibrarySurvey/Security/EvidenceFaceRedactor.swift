import CoreImage
import Foundation
import ImageIO
import Vision

enum FaceRedactionError: LocalizedError {
  case invalidJPEG
  case renderFailed

  var errorDescription: String? {
    switch self {
    case .invalidJPEG: "Face redaction could not decode JPEG evidence."
    case .renderFailed: "Face redaction could not render protected JPEG evidence."
    }
  }
}

struct EvidenceRedactionRecord: Codable {
  let path: String
  let policy: String
  let detector: String
  let status: String
  let faceCount: Int
}

enum EvidenceFaceRedactor {
  private static let detector = "Vision.VNDetectFaceRectanglesRequest"
  private static let context = CIContext(options: [.cacheIntermediates: false])

  /// Returns original bytes only when redaction is explicitly disabled. If enabled,
  /// detection, decoding, and rendering errors throw so unredacted evidence is never sealed.
  static func process(
    jpeg: Data,
    path: String,
    enabled: Bool
  ) throws -> (data: Data, record: EvidenceRedactionRecord) {
    guard enabled else {
      return (
        jpeg,
        EvidenceRedactionRecord(
          path: path, policy: "disabled", detector: detector,
          status: "bypassed", faceCount: 0
        )
      )
    }
    guard let source = CGImageSourceCreateWithData(jpeg as CFData, nil),
          let cgImage = CGImageSourceCreateImageAtIndex(source, 0, nil)
    else { throw FaceRedactionError.invalidJPEG }

    let orientation = CGImagePropertyOrientation(
      rawValue: (CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any])?[
        kCGImagePropertyOrientation
      ] as? UInt32 ?? 1
    ) ?? .up
    let request = VNDetectFaceRectanglesRequest()
    try VNImageRequestHandler(cgImage: cgImage, orientation: orientation).perform([request])
    let faces = request.results ?? []
    guard !faces.isEmpty else {
      return (
        jpeg,
        EvidenceRedactionRecord(
          path: path, policy: "required", detector: detector,
          status: "no_faces", faceCount: 0
        )
      )
    }

    let image = CIImage(cgImage: cgImage).oriented(forExifOrientation: Int32(orientation.rawValue))
    let pixelated = image
      .clampedToExtent()
      .applyingFilter("CIPixellate", parameters: [kCIInputScaleKey: 28])
      .cropped(to: image.extent)
    var output = image
    for face in faces {
      let box = face.boundingBox
      let rect = CGRect(
        x: image.extent.minX + box.minX * image.extent.width,
        y: image.extent.minY + box.minY * image.extent.height,
        width: box.width * image.extent.width,
        height: box.height * image.extent.height
      ).insetBy(dx: -12, dy: -12).intersection(image.extent)
      let mask = CIImage(color: .white).cropped(to: rect)
      output = pixelated.applyingFilter(
        "CIBlendWithMask",
        parameters: [kCIInputBackgroundImageKey: output, kCIInputMaskImageKey: mask]
      )
    }
    guard let rendered = context.jpegRepresentation(
      of: output.cropped(to: image.extent),
      colorSpace: CGColorSpaceCreateDeviceRGB(),
      options: [kCGImageDestinationLossyCompressionQuality as CIImageRepresentationOption: 0.9]
    ) else { throw FaceRedactionError.renderFailed }
    return (
      rendered,
      EvidenceRedactionRecord(
        path: path, policy: "required", detector: detector,
        status: "redacted", faceCount: faces.count
      )
    )
  }
}
