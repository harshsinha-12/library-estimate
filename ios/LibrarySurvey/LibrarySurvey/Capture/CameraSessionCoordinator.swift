import Foundation

/// Exclusive camera lock. Apple will not let RoomPlan, a second AR session, and
/// `UIImagePicker` / `AVCaptureSession` share the camera.
@MainActor
final class CameraSessionCoordinator: ObservableObject {
  enum Owner: String, Equatable {
    case idle
    case roomPlan
    case shelfAR
    case stillCamera
  }

  enum RGBEvidenceMode: String, Codable, Equatable {
    case sampledDuringScan = "sampled_during_scan"
    case sequentialFinalFrame = "sequential_final_frame"
  }

  @Published private(set) var owner: Owner = .idle
  @Published private(set) var statusLine =
    "Camera idle. RoomPlan will own it for Pass A."

  var geometrySessionActive: Bool {
    owner == .roomPlan || owner == .shelfAR
  }

  var canUseStillCamera: Bool { owner == .idle }

  @discardableResult
  func tryAcquire(_ next: Owner) -> Bool {
    guard next != .idle else { return owner == .idle }
    if owner == next { return true }
    guard owner == .idle else { return false }
    owner = next
    statusLine = status(for: next)
    return true
  }

  func release(_ current: Owner) {
    guard owner == current else { return }
    owner = .idle
    statusLine = "Camera idle. The next pass may start after this session stopped."
  }

  func reset() {
    owner = .idle
    statusLine = "Camera idle. RoomPlan will own it for Pass A."
  }

  private func status(for owner: Owner) -> String {
    switch owner {
    case .idle:
      "Camera idle. The next pass may start after this session stopped."
    case .roomPlan:
      "RoomPlan owns the camera. RGB is copied from this AR session. Optical zoom is not available."
    case .shelfAR:
      "Shelf Pass B owns the camera. This AR view started after RoomPlan released the session."
    case .stillCamera:
      "Pass C still camera. Close-ups run only after RoomPlan and the shelf AR view have stopped."
    }
  }
}
