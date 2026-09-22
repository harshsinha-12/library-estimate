import Combine
import Foundation
import os
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
  let detail: String?
}

@MainActor
final class AstraLiveSession: ObservableObject {
  static let minInterval: TimeInterval = 2
  private static let logger = Logger(
    subsystem: "dev.harshsinha.LibrarySurvey",
    category: "astra-live"
  )

  @Published var status = ""
  @Published var latest: AstraLiveResponse?
  private var lastAttempt = Date.distantPast
  private var prepared = false
  private var sampledFace = false
  private var inFlight = false

  func resetFace() {
    sampledFace = false
  }

  var caption: String {
    if let assist = latest?.assist {
      let count = assist.provisionalCount.map(String.init) ?? "?"
      return "Astra-live assist · about \(count) in frame · not inventory"
    }
    if let latest, latest.status == "skipped" {
      return "Astra-live skipped (\(latest.reason ?? "sampled")) · not inventory"
    }
    if status.isEmpty {
      return "Astra-live assist · connecting · not inventory"
    }
    return status
  }

  func consider(
    jpeg: Data?,
    capturePass: String,
    draft: SurveyDraft,
    backendURL: URL?,
    qualityMessages: [String],
    provisionalCount: Int,
    unreadableSlots: [String],
    force: Bool = false
  ) async {
    guard let backendURL else {
      status = "Astra-live: backend URL missing in Settings · not inventory"
      Self.logger.error("Astra-live skipped: backend URL missing")
      return
    }
    guard !inFlight else { return }
    let due = Date().timeIntervalSince(lastAttempt) >= Self.minInterval
    guard force || due else { return }
    guard let jpeg, let compact = Self.sampledJpeg(jpeg), compact.count > 32 else {
      status = "Astra-live waiting for a frame · not inventory"
      return
    }
    lastAttempt = Date()
    sampledFace = true
    inFlight = true
    defer { inFlight = false }
    status = "Astra-live assist · posting · not inventory"
    do {
      try await prepare(draft: draft, backendURL: backendURL)
      let url = try OperatorSession.apiURL(
        backendURL,
        path: "v1/surveys/\(draft.id.uuidString)/astra-live"
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
        let body = String(data: data, encoding: .utf8) ?? ""
        let snippet = String(body.prefix(180)).replacingOccurrences(of: "\n", with: " ")
        status = "Astra-live HTTP \(code)\(snippet.isEmpty ? "" : ": \(snippet)") · not inventory"
        Self.logger.error("Astra-live HTTP \(code, privacy: .public) \(snippet, privacy: .public)")
        return
      }
      do {
        latest = try JSONCoding.decoder().decode(AstraLiveResponse.self, from: data)
        status = caption
      } catch {
        let snippet = String(data: data, encoding: .utf8).map { String($0.prefix(180)) } ?? ""
        status = "Astra-live decode failed: \(error.localizedDescription) · not inventory"
        Self.logger.error(
          "Astra-live decode \(error.localizedDescription, privacy: .public) \(snippet, privacy: .public)"
        )
      }
    } catch {
      status = "Astra-live failed: \(error.localizedDescription) · not inventory"
      Self.logger.error("Astra-live \(error.localizedDescription, privacy: .public)")
    }
  }

  private func prepare(draft: SurveyDraft, backendURL: URL) async throws {
    if prepared { return }
    var request = URLRequest(url: try OperatorSession.apiURL(backendURL, path: "v1/surveys"))
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
