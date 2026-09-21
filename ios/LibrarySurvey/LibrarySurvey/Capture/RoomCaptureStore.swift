import ARKit
import Foundation
import RoomPlan

@MainActor
final class RoomCaptureStore: ObservableObject {
  enum State: Equatable {
    case ready
    case capturing
    case paused
    case processing
    case captured
    case unsupported
    case failed(String)
  }

  @Published private(set) var state: State
  @Published private(set) var rooms: [CapturedRoom] = []
  @Published private(set) var samples: [FrameSample] = []
  @Published private(set) var floorPlan: FloorPlanLayout?
  @Published private(set) var rgbEvidenceMode: CameraSessionCoordinator.RGBEvidenceMode =
    .sampledDuringScan
  @Published private(set) var sequentialFallbackDetail: String?
  @Published var roomName = "Library"

  private var session: RoomCaptureSession?
  private let sampler = FrameSampler()
  private var pauseRequested = false
  private var collectedLiveRGB = false
  private let camera: CameraSessionCoordinator
  private(set) var startedAt: Date?
  private(set) var monotonicAnchor: Double?

  init(camera: CameraSessionCoordinator) {
    self.camera = camera
    state = RoomCaptureSession.isSupported ? .ready : .unsupported
  }

  func attach(session: RoomCaptureSession) {
    self.session = session
    _ = camera.tryAcquire(.roomPlan)
  }

  var arSession: ARSession? { session?.arSession }

  func start() {
    guard RoomCaptureSession.isSupported, let session else {
      state = .unsupported
      return
    }
    guard camera.tryAcquire(.roomPlan) else {
      state = .failed("The camera is in use by another pass. Finish that session first.")
      return
    }
    startedAt = Date()
    monotonicAnchor = MonotonicClock.now
    pauseRequested = false
    collectedLiveRGB = false
    rgbEvidenceMode = .sampledDuringScan
    sequentialFallbackDetail = nil
    samples = []
    rooms = []
    floorPlan = nil
    state = .capturing
    session.run(configuration: RoomCaptureSession.Configuration())
    sampler.start(session: session.arSession)
  }

  func stop() {
    if state == .paused {
      state = .captured
      pauseLiveCamera()
      return
    }
    guard state == .capturing else { return }
    finishSampling()
    pauseRequested = false
    state = .processing
    session?.stop()
  }

  func pause() {
    guard state == .capturing else { return }
    finishSampling()
    pauseRequested = true
    state = .processing
    session?.stop()
  }

  func resume() {
    guard state == .paused, let session else { return }
    guard camera.tryAcquire(.roomPlan) else { return }
    pauseRequested = false
    state = .capturing
    session.run(configuration: RoomCaptureSession.Configuration())
    sampler.start(session: session.arSession)
  }

  func currentFrameSample() -> FrameSample? { sampler.samples.last }

  func didFinish(room: CapturedRoom?, error: Error?) {
    if let error {
      state = .failed(error.localizedDescription)
      pauseLiveCamera()
      return
    }
    guard let room else {
      state = .failed("RoomPlan did not return a processed room.")
      pauseLiveCamera()
      return
    }
    rooms.append(room)
    state = pauseRequested ? .paused : .captured
    pauseLiveCamera()
    Task { await refreshFloorPlan() }
  }

  func releaseGeometrySession() {
    sampler.stop(captureFallback: false)
    pauseLiveCamera()
    session = nil
    camera.release(.roomPlan)
  }

  func didDismantleCaptureView() {
    session = nil
    camera.release(.roomPlan)
  }

  func releaseAfterSeal() {
    sampler.releaseSamples()
    session = nil
    rooms = []
    samples = []
    floorPlan = nil
    sequentialFallbackDetail = nil
    rgbEvidenceMode = .sampledDuringScan
    collectedLiveRGB = false
    camera.release(.roomPlan)
  }

  func reset() {
    sampler.releaseSamples()
    samples = []
    rooms = []
    floorPlan = nil
    sequentialFallbackDetail = nil
    rgbEvidenceMode = .sampledDuringScan
    collectedLiveRGB = false
    pauseRequested = false
    startedAt = nil
    monotonicAnchor = nil
    state = RoomCaptureSession.isSupported ? .ready : .unsupported
    if session != nil {
      _ = camera.tryAcquire(.roomPlan)
    } else {
      camera.release(.roomPlan)
    }
  }

  func refreshFloorPlan() async {
    let captured = rooms
    let name = roomName
    guard !captured.isEmpty else { return }
    guard let structure = try? await RoomPlanAdapter.portableStructure(
      from: captured,
      defaultLabel: name
    ),
      let layout = try? FloorPlanLayout(structure)
    else { return }
    floorPlan = layout
  }

  private func finishSampling() {
    sampler.stop()
    samples.append(contentsOf: sampler.samples)
    if sampler.liveSampleCount > 0 { collectedLiveRGB = true }
    applyRGBEvidenceMode()
  }

  private func applyRGBEvidenceMode() {
    if collectedLiveRGB {
      rgbEvidenceMode = .sampledDuringScan
      sequentialFallbackDetail = nil
      return
    }
    rgbEvidenceMode = .sequentialFinalFrame
    if samples.isEmpty {
      sequentialFallbackDetail =
        "Sequential fallback: RoomPlan kept the processed room. No RGB frame was available during the scan. Shelf Pass B captures evidence after this session is released."
    } else {
      sequentialFallbackDetail =
        "Sequential fallback: RoomPlan kept the processed room. Dual-camera RGB during the scan was unavailable, so this package uses the final AR frame. Shelf Pass B starts after this session is released."
    }
  }

  private func pauseLiveCamera() {
    session?.arSession.pause()
  }
}
