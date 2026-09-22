import Foundation

enum AppConfiguration {
  static let schemaVersion = "1.0.0"
  static let defaultBackendURL = URL(string: "http://192.168.29.178:8000")!
  static let retentionPolicyID = "demo-v1"
  static let appVersion = "0.1.0"
  static let appBuild = "1"
  static let roomPlanFormat = "library-roomplan-1.0"
  static var faceRedactionRequired: Bool {
    Bundle.main.object(forInfoDictionaryKey: "LibraryFaceRedactionRequired") as? Bool ?? false
  }

  static func localeDefaults(countryCode: String) -> (currency: String, market: String) {
    switch countryCode.uppercased() {
    case "IN": ("INR", "en-IN")
    case "IT": ("EUR", "it-IT")
    case "JP": ("JPY", "ja-JP")
    case "GB": ("GBP", "en-GB")
    case "US": ("USD", "en-US")
    default: ("USD", "en-US")
    }
  }
}

