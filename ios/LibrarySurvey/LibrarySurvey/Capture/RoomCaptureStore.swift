import Foundation
import RoomPlan

@MainActor
final class RoomCaptureStore: ObservableObject {
  enum State: Equatable {
    case ready
    case capturing
    case processing
    case captured
    case unsupported
    case failed(String)
  }

  @Published private(set) var state: State
  @Published private(set) var rooms: [CapturedRoom] = []
  @Published private(set) var samples: [FrameSample] = []
  @Published var roomName = "Library"

  private var session: RoomCaptureSession?
  private let sampler = FrameSampler()
  private(set) var startedAt: Date?
  private(set) var monotonicAnchor: Double?

  init() {
    state = RoomCaptureSession.isSupported ? .ready : .unsupported
  }

  func attach(session: RoomCaptureSession) {
    self.session = session
  }

  func start() {
    guard RoomCaptureSession.isSupported, let session else {
      state = .unsupported
      return
    }
    startedAt = Date()
    monotonicAnchor = MonotonicClock.now
    state = .capturing
    session.run(configuration: RoomCaptureSession.Configuration())
    sampler.start(session: session.arSession)
  }

  func stop() {
    guard state == .capturing else { return }
    sampler.stop()
    samples = sampler.samples
    state = .processing
    session?.stop()
  }

  func currentFrameSample() -> FrameSample? { sampler.samples.last }

  func didFinish(room: CapturedRoom?, error: Error?) {
    if let error {
      state = .failed(error.localizedDescription)
      return
    }
    guard let room else {
      state = .failed("RoomPlan did not return a processed room.")
      return
    }
    rooms.append(room)
    state = .captured
  }

  func releaseAfterSeal() {
    sampler.releaseSamples()
    session = nil
    rooms = []
    samples = []
  }

  func reset() {
    releaseAfterSeal()
    startedAt = nil
    monotonicAnchor = nil
    state = RoomCaptureSession.isSupported ? .ready : .unsupported
  }
}
