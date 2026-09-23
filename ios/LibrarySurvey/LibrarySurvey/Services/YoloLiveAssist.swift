import Foundation
import os
import UIKit

struct YoloLiveBox: Decodable {
  let x: Double
  let y: Double
  let width: Double
  let height: Double
  let confidence: Double
  let label: String?
  let title: String?
}

struct YoloLiveResponse: Decodable {
  let enabled: Bool
  let reason: String?
  let install: String?
  let pipeline: String?
  let count: Int?
  let boxes: [YoloLiveBox]?
}

@MainActor
final class YoloLiveSession: ObservableObject {
  static let minInterval: TimeInterval = 2
  private static let logger = Logger(
    subsystem: "dev.harshsinha.LibrarySurvey",
    category: "yolo-live"
  )

  @Published var enabled = false
  @Published var installHint = ""
  @Published var boxes: [ShelfOverlayBox] = []
  private var lastAttempt = Date.distantPast
  private var inFlight = false

  func consider(jpeg: Data?, backendURL: URL?) async {
    guard let backendURL else {
      installHint = "Set Backend URL to the Mac. YOLO runs there, not on the phone."
      return
    }
    guard !inFlight else { return }
    guard Date().timeIntervalSince(lastAttempt) >= Self.minInterval else { return }
    guard let jpeg, let compact = Self.sampledJpeg(jpeg), compact.count > 32 else { return }
    lastAttempt = Date()
    inFlight = true
    defer { inFlight = false }
    do {
      let url = try OperatorSession.apiURL(backendURL, path: "v1/yolo-live")
      var request = URLRequest(url: url)
      request.httpMethod = "POST"
      request.timeoutInterval = 30
      request.setValue("application/json", forHTTPHeaderField: "Content-Type")
      request.httpBody = try JSONCoding.encoder(pretty: false).encode(
        YoloLivePayload(imageBase64: compact.base64EncodedString())
      )
      let (data, response) = try await OperatorSession.data(for: request)
      let code = (response as? HTTPURLResponse)?.statusCode ?? 0
      guard (200..<300).contains(code) else {
        installHint = "YOLO HTTP \(code). Is uvicorn on, and pip install -e '.[yolo]' done on the Mac?"
        Self.logger.error("YOLO-live HTTP \(code, privacy: .public)")
        return
      }
      let decoded = try JSONCoding.decoder().decode(YoloLiveResponse.self, from: data)
      enabled = decoded.enabled
      if decoded.enabled {
        installHint = ""
        boxes = (decoded.boxes ?? []).enumerated().map { index, box in
          let score = String(format: "%.2f", box.confidence)
          let name = (box.title?.isEmpty == false ? box.title : nil) ?? box.label ?? "book"
          return ShelfOverlayBox(
            id: "yolo-\(index)",
            box: CGRect(x: box.x, y: box.y, width: box.width, height: box.height),
            caption: "\(name) \(score)",
            source: "yolo",
            readable: true
          )
        }
      } else {
        boxes = []
        installHint = decoded.install
          ?? "On the Mac: pip install -e '.[yolo]' then restart uvicorn. Rebuild this app."
      }
    } catch {
      installHint = "YOLO-live failed: \(error.localizedDescription). pip install -e '.[yolo]' on the Mac."
      Self.logger.error("YOLO-live \(error.localizedDescription, privacy: .public)")
    }
  }

  private static func sampledJpeg(_ data: Data) -> Data? {
    guard let image = UIImage(data: data) else { return data }
    let first = image.jpegData(compressionQuality: 0.45) ?? data
    if first.count <= 350_000 { return first }
    return image.jpegData(compressionQuality: 0.25) ?? first
  }
}

private struct YoloLivePayload: Encodable {
  let imageBase64: String
}
