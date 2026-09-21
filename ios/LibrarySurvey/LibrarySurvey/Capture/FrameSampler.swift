import ARKit
import CoreImage
import Foundation
import UIKit

@MainActor
final class FrameSampler {
  private let context = CIContext()
  private var timer: Timer?
  private weak var session: ARSession?
  private(set) var samples: [FrameSample] = []

  func start(session: ARSession, interval: TimeInterval = 1.0) {
    stop()
    self.session = session
    samples = []
    timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
      Task { @MainActor in self?.sampleCurrentFrame() }
    }
  }

  func stop(captureFallback: Bool = true) {
    timer?.invalidate()
    timer = nil
    if captureFallback && samples.isEmpty {
      sampleCurrentFrame()
    }
    session = nil
  }

  func releaseSamples() {
    stop(captureFallback: false)
    samples = []
  }

  private func sampleCurrentFrame() {
    guard let frame = session?.currentFrame else { return }
    let image = CIImage(cvPixelBuffer: frame.capturedImage)
    guard let cgImage = context.createCGImage(image, from: image.extent),
          let jpeg = UIImage(cgImage: cgImage).jpegData(compressionQuality: 0.78)
    else { return }
    let matrix = frame.camera.transform
    let transform = [matrix.columns.0, matrix.columns.1, matrix.columns.2, matrix.columns.3]
      .flatMap { [$0.x, $0.y, $0.z, $0.w] }
    samples.append(
      FrameSample(
        id: UUID(),
        capturedAt: Date(),
        monotonicSeconds: MonotonicClock.now,
        cameraTransform: transform,
        jpegData: jpeg
      )
    )
  }
}

