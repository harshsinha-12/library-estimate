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
  private(set) var projections: [UUID: (camera: ARCamera, orientation: UIInterfaceOrientation)] = [:]
  private(set) var liveSampleCount = 0

  func start(session: ARSession, interval: TimeInterval = 1.0) {
    stop(captureFallback: false)
    self.session = session
    samples = []
    projections = [:]
    liveSampleCount = 0
    timer = Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in
      Task { @MainActor in self?.sampleCurrentFrame(countsAsLive: true) }
    }
  }

  func stop(captureFallback: Bool = true) {
    timer?.invalidate()
    timer = nil
    if captureFallback && samples.isEmpty {
      sampleCurrentFrame(countsAsLive: false)
    }
    session = nil
  }

  func releaseSamples() {
    stop(captureFallback: false)
    samples = []
    projections = [:]
    liveSampleCount = 0
  }

  private func sampleCurrentFrame(countsAsLive: Bool) {
    guard let frame = session?.currentFrame else { return }
    let image = CIImage(cvPixelBuffer: frame.capturedImage).oriented(displayOrientation)
    guard let cgImage = context.createCGImage(image, from: image.extent),
          let jpeg = UIImage(cgImage: cgImage).jpegData(compressionQuality: 0.78)
    else { return }
    let matrix = frame.camera.transform
    let transform = [matrix.columns.0, matrix.columns.1, matrix.columns.2, matrix.columns.3]
      .flatMap { [$0.x, $0.y, $0.z, $0.w] }
    let id = UUID()
    projections[id] = (frame.camera, interfaceOrientation)
    samples.append(
      FrameSample(
        id: id,
        capturedAt: Date(),
        monotonicSeconds: MonotonicClock.now,
        cameraTransform: transform,
        jpegData: jpeg
      )
    )
    if countsAsLive { liveSampleCount += 1 }
  }

  private var displayOrientation: CGImagePropertyOrientation {
    switch UIDevice.current.orientation {
    case .landscapeLeft: .up
    case .landscapeRight: .down
    case .portraitUpsideDown: .left
    default: .right
    }
  }

  private var interfaceOrientation: UIInterfaceOrientation {
    switch UIDevice.current.orientation {
    case .landscapeLeft: .landscapeRight
    case .landscapeRight: .landscapeLeft
    case .portraitUpsideDown: .portraitUpsideDown
    default: .portrait
    }
  }
}
