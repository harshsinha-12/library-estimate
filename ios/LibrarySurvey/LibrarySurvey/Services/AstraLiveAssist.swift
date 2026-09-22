import Combine
import Foundation
import UIKit

struct AstraLiveQuality: Decodable {
  let blur: Bool?
  let glare: Bool?
  let readable: Bool?
  let notes: String?
}

struct AstraLiveAssistPayload: Decodable {
  let pipeline: String?
  let authority: String?
  let capturePass: String?
  let quality: AstraLiveQuality?
  let provisionalCount: Int?
  let unreadableSlots: [String]?
  let recaptureHint: String?
  let confidence: Double?
  let rationale: String?
}

struct AstraLiveResponse: Decodable {
  let status: String
  let reason: String?
  let pipeline: String?
  let authority: String?
  let review: String?
  let invented: Bool?
  let assist: AstraLiveAssistPayload?
}

@MainActor
final class AstraLiveSession: ObservableObject {
  static let maxCalls = 6
  static let minInterval: TimeInterval = 8

  @Published var status = ""
  @Published var latest: AstraLiveResponse?
  private var calls = 0
  private var lastAttempt = Date.distantPast
  private var prepared = false
  private var sampledFace = false

  func resetFace() {
    sampledFace = false
  }

  var caption: String {
    if let assist = latest?.assist {
      let count = assist.provisionalCount.map(String.init) ?? "?"
      let hint = assist.recaptureHint ?? assist.rationale ?? "sampled"
      return "Astra-live assist · about \(count) in frame · \(hint) · not inventory"
    }
    if let latest, latest.status == "skipped" {
      return "Astra-live skipped (\(latest.reason ?? "sampled")) · not inventory"
    }
    return status
  }

  func consider(
    jpeg: Data,
    capturePass: String,
    draft: SurveyDraft,
    backendURL: URL?,
    qualityMessages: [String],
    provisionalCount: Int,
    unreadableSlots: [String],
    force: Bool = false
  ) async {
    guard let backendURL, calls < Self.maxCalls else { return }
    let due = Date().timeIntervalSince(lastAttempt) >= Self.minInterval
    let uncertain = !qualityMessages.isEmpty || !unreadableSlots.isEmpty
    let first = !sampledFace
    guard force || (due && (uncertain || first)) else { return }
    guard let compact = Self.sampledJpeg(jpeg), compact.count > 32 else { return }
    lastAttempt = Date()
    sampledFace = true
    do {
      try await prepare(draft: draft, backendURL: backendURL)
      let url = backendURL.appendingPathComponent(
        "v1/surveys/\(draft.id.uuidString)/astra-live"
      )
      var request = URLRequest(url: url)
      request.httpMethod = "POST"
      request.timeoutInterval = 45
      request.setValue("application/json", forHTTPHeaderField: "Content-Type")
      request.httpBody = try JSONCoding.encoder(pretty: false).encode(
        AstraLivePayload(
          capturePass: capturePass,
          imageBase64: compact.base64EncodedString(),
          qualityMessages: qualityMessages,
          provisionalCount: provisionalCount,
          unreadableSlots: unreadableSlots
        )
      )
      let (data, response) = try await OperatorSession.data(for: request)
      let code = (response as? HTTPURLResponse)?.statusCode ?? 0
      guard (200..<300).contains(code) else {
        status = "Astra-live unavailable · assist only, not inventory"
        return
      }
      latest = try JSONCoding.decoder().decode(AstraLiveResponse.self, from: data)
      if latest?.status == "assist" { calls += 1 }
      status = caption
    } catch {
      status = "Astra-live paused · keep scanning. Assist is not inventory."
    }
  }

  private func prepare(draft: SurveyDraft, backendURL: URL) async throws {
    if prepared { return }
    var request = URLRequest(url: backendURL.appendingPathComponent("v1/surveys"))
    request.httpMethod = "POST"
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.setValue("create-\(draft.id.uuidString)", forHTTPHeaderField: "Idempotency-Key")
    request.httpBody = try JSONCoding.encoder(pretty: false).encode(
      AstraLiveSurveyCreate(
        surveyId: draft.id,
        displayName: draft.displayName.isEmpty ? "Live survey" : draft.displayName,
        geography: draft.geography
      )
    )
    let (_, response) = try await OperatorSession.data(for: request)
    let code = (response as? HTTPURLResponse)?.statusCode ?? 0
    guard (200..<300).contains(code) || code == 409 else {
      throw URLError(.badServerResponse)
    }
    prepared = true
  }

  private static func sampledJpeg(_ data: Data) -> Data? {
    guard let image = UIImage(data: data) else { return data }
    let first = image.jpegData(compressionQuality: 0.45) ?? data
    if first.count <= 350_000 { return first }
    return image.jpegData(compressionQuality: 0.25) ?? first
  }
}

private struct AstraLivePayload: Encodable {
  let capturePass: String
  let imageBase64: String
  let qualityMessages: [String]
  let provisionalCount: Int
  let unreadableSlots: [String]
}

private struct AstraLiveSurveyCreate: Encodable {
  let surveyId: UUID
  let displayName: String
  let geography: SurveyGeography
}
