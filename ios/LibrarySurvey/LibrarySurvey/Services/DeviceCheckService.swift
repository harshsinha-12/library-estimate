import AVFoundation
import Foundation
import RoomPlan
import UIKit

struct DeviceCheckItem: Identifiable {
  let id: String
  let title: String
  let passed: Bool
  let detail: String
  let required: Bool
}

@MainActor
enum DeviceCheckService {
  static func snapshot(locationAuthorized: Bool) -> [DeviceCheckItem] {
    UIDevice.current.isBatteryMonitoringEnabled = true
    let batteryLevel = UIDevice.current.batteryLevel
    let camera = AVCaptureDevice.authorizationStatus(for: .video)
    let microphone = AVAudioApplication.shared.recordPermission
    let freeBytes = try? FileManager.default.attributesOfFileSystem(
      forPath: NSHomeDirectory()
    )[.systemFreeSize] as? NSNumber
    let freeGB = freeBytes?.doubleValue ?? 0
    return [
      DeviceCheckItem(
        id: "lidar",
        title: "RoomPlan / LiDAR",
        passed: RoomCaptureSession.isSupported,
        detail: RoomCaptureSession.isSupported ? "Supported" : "A LiDAR iPhone or iPad is required",
        required: true
      ),
      DeviceCheckItem(
        id: "storage",
        title: "Free storage",
        passed: freeGB >= 1_000_000_000,
        detail: String(format: "%.1f GB available", freeGB / 1_000_000_000),
        required: true
      ),
      DeviceCheckItem(
        id: "camera",
        title: "Camera",
        passed: camera == .authorized,
        detail: authorizationLabel(camera),
        required: true
      ),
      DeviceCheckItem(
        id: "microphone",
        title: "Microphone",
        passed: microphone == .granted,
        detail: String(describing: microphone),
        required: true
      ),
      DeviceCheckItem(
        id: "location",
        title: "Location or manual fallback",
        passed: locationAuthorized,
        detail: locationAuthorized ? "Available" : "Manual country and city will be used",
        required: false
      ),
      DeviceCheckItem(
        id: "battery",
        title: "Battery",
        passed: batteryLevel < 0 || batteryLevel >= 0.2,
        detail: batteryLevel < 0
          ? "Battery level unavailable"
          : "\(Int(batteryLevel * 100))% available",
        required: false
      ),
      DeviceCheckItem(
        id: "network",
        title: "Network",
        passed: true,
        detail: "Optional during capture; upload resumes when the backend is reachable",
        required: false
      ),
    ]
  }

  static func requestMediaPermissions() async {
    _ = await AVCaptureDevice.requestAccess(for: .video)
    _ = await AVAudioApplication.requestRecordPermission()
  }

  private static func authorizationLabel(_ status: AVAuthorizationStatus) -> String {
    switch status {
    case .authorized: "Authorized"
    case .denied: "Denied"
    case .restricted: "Restricted"
    case .notDetermined: "Not requested"
    @unknown default: "Unknown"
    }
  }
}
