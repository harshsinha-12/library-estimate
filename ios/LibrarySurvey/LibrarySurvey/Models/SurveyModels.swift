import Foundation

enum GeographySource: String, Codable, CaseIterable {
  case gps
  case manual
  case mixed
}

struct SurveyGeography: Codable, Equatable {
  var countryCode: String
  var region: String?
  var city: String
  var currency: String
  var market: String
  var source: GeographySource
  var latitude: Double?
  var longitude: Double?
  var preciseLocationConsent: Bool
}

struct SurveyConsent: Codable, Equatable {
  var video: Bool
  var audio: Bool
  var location: Bool
  var retentionPolicyId: String
}

struct SurveyDraft: Codable, Identifiable, Equatable {
  let id: UUID
  var displayName: String
  var geography: SurveyGeography
  var consent: SurveyConsent
  let createdAt: Date

  static func empty() -> SurveyDraft {
    SurveyDraft(
      id: UUID(),
      displayName: "",
      geography: SurveyGeography(
        countryCode: "IN",
        region: nil,
        city: "",
        currency: "INR",
        market: "en-IN",
        source: .manual,
        latitude: nil,
        longitude: nil,
        preciseLocationConsent: false
      ),
      consent: SurveyConsent(
        video: false,
        audio: false,
        location: false,
        retentionPolicyId: AppConfiguration.retentionPolicyID
      ),
      createdAt: Date()
    )
  }
}

struct WrittenNote: Codable, Identifiable, Equatable {
  let id: UUID
  let text: String
  let createdAt: Date
  let monotonicSeconds: Double
}

struct FrameSample: Identifiable {
  let id: UUID
  let capturedAt: Date
  let monotonicSeconds: Double
  let cameraTransform: [Float]
  let jpegData: Data
}

struct PackageFile: Codable, Equatable {
  let path: String
  let mimeType: String
  let bytes: Int
  let sha256: String
}

struct CaptureManifest: Codable {
  struct AppIdentity: Codable { let version: String; let build: String }
  struct DeviceIdentity: Codable {
    let model: String
    let systemVersion: String
    let supportsLidar: Bool
    let roomplanVersion: String?
    let visionVersion: String?
  }
  struct Timing: Codable {
    let startedAt: Date
    let endedAt: Date
    let monotonicAnchorSeconds: Double
    let timezone: String
  }

  let schemaVersion: String
  let surveyId: UUID
  let sessionId: UUID
  let app: AppIdentity
  let device: DeviceIdentity
  let geography: SurveyGeography
  let consent: SurveyConsent
  let timing: Timing
  let captureState: String
  let captureModes: [String]
  let files: [PackageFile]
}

struct SealedSurveyPackage: Identifiable {
  var id: UUID { surveyId }
  let surveyId: UUID
  let rootURL: URL
  let manifestURL: URL
  let svgURL: URL
  let usdzURL: URL
  let manifest: CaptureManifest
}

