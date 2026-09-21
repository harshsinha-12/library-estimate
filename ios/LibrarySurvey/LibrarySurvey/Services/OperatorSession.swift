import Foundation
import Security

enum OperatorCredentials {
  private static let service = "dev.harshsinha.LibrarySurvey.operator"
  private static let account = "backend-token"

  static func load() -> String {
    let query: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account,
      kSecReturnData as String: true,
      kSecMatchLimit as String: kSecMatchLimitOne
    ]
    var result: CFTypeRef?
    guard SecItemCopyMatching(query as CFDictionary, &result) == errSecSuccess,
          let data = result as? Data,
          let token = String(data: data, encoding: .utf8)
    else { return "" }
    return token
  }

  static func save(_ token: String) {
    let query: [String: Any] = [
      kSecClass as String: kSecClassGenericPassword,
      kSecAttrService as String: service,
      kSecAttrAccount as String: account
    ]
    SecItemDelete(query as CFDictionary)
    let trimmed = token.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else { return }
    var attributes = query
    attributes[kSecValueData as String] = Data(trimmed.utf8)
    attributes[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly
    SecItemAdd(attributes as CFDictionary, nil)
  }
}

enum OperatorSession {
  private static func authorized(_ request: URLRequest) throws -> URLRequest {
    var request = request
    let token = OperatorCredentials.load()
    if !token.isEmpty {
      guard request.url?.scheme?.lowercased() == "https" else {
        throw URLError(.secureConnectionFailed)
      }
      request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
    }
    return request
  }

  static func data(for request: URLRequest) async throws -> (Data, URLResponse) {
    try await URLSession.shared.data(for: authorized(request))
  }

  static func data(from url: URL) async throws -> (Data, URLResponse) {
    try await data(for: URLRequest(url: url))
  }

  static func upload(for request: URLRequest, fromFile fileURL: URL) async throws -> (Data, URLResponse) {
    try await URLSession.shared.upload(for: authorized(request), fromFile: fileURL)
  }
}
