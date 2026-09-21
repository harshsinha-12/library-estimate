import Foundation

@MainActor
final class SurveyUploadService: ObservableObject {
  @Published private(set) var progress = 0.0
  @Published private(set) var status = "Ready to upload"
  @Published private(set) var errorMessage: String?
  @Published private(set) var isUploading = false
  @Published private(set) var isChecking = false

  var isBusy: Bool { isUploading || isChecking }

  private struct UploadState: Codable {
    var completedPaths: Set<String>
  }

  func upload(
    package: SealedSurveyPackage,
    draft: SurveyDraft,
    backendURL: URL = AppConfiguration.defaultBackendURL
  ) async {
    guard !isBusy else { return }
    isUploading = true
    errorMessage = nil
    status = "Connecting to \(backendURL.host ?? backendURL.absoluteString)"
    await Task.yield()
    try? await Task.sleep(nanoseconds: 250_000_000)
    do {
      try await ensureSurvey(draft, backendURL: backendURL)
      var state = loadState(package: package)
      for (index, file) in package.manifest.files.enumerated() {
        if !state.completedPaths.contains(file.path) {
          status = "Uploading \(file.path)"
          try await uploadFile(file, package: package, backendURL: backendURL)
          state.completedPaths.insert(file.path)
          try saveState(state, package: package)
        }
        progress = Double(index + 1) / Double(package.manifest.files.count + 1)
      }
      status = "Validating and sealing"
      try await seal(package, backendURL: backendURL)
      progress = 1
      status = "Uploaded and accepted"
      errorMessage = nil
    } catch {
      status = "Upload paused"
      errorMessage = Self.describe(error, backendURL: backendURL)
    }
    isUploading = false
  }

  func report(_ message: String) {
    status = "Upload paused"
    errorMessage = message
  }

  func ping(backendURL: URL) async {
    guard !isBusy else { return }
    LocalNetworkPrompter.shared.promptIfNeeded()
    isChecking = true
    status = "Checking \(backendURL.absoluteString)"
    do {
      try await Self.waitForHealthz(backendURL: backendURL)
      status = "Backend reachable at \(backendURL.absoluteString)"
      errorMessage = nil
    } catch {
      status = "Could not reach backend"
      errorMessage = Self.describe(error, backendURL: backendURL)
    }
    isChecking = false
  }

  nonisolated private static func waitForHealthz(backendURL: URL) async throws {
    var lastError: Error = URLError(.cannotConnectToHost)
    for attempt in 1...5 {
      do {
        try await hitHealthz(backendURL: backendURL)
        return
      } catch {
        lastError = error
        if attempt < 5 {
          try await Task.sleep(nanoseconds: 1_200_000_000)
        }
      }
    }
    throw lastError
  }

  nonisolated private static func hitHealthz(backendURL: URL) async throws {
    var request = URLRequest(url: try makeAPIURL(backendURL, path: "healthz"))
    request.timeoutInterval = 8
    request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
    let (_, response) = try await URLSession.shared.data(for: request)
    guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
      throw URLError(.badServerResponse)
    }
  }

  private static func describe(_ error: Error, backendURL: URL) -> String {
    if let urlError = error as? URLError {
      switch urlError.code {
      case .cannotConnectToHost, .cannotFindHost, .networkConnectionLost, .notConnectedToInternet,
           .timedOut, .cancelled:
        return """
        Could not connect to \(backendURL.absoluteString). \
        Tap Allow on Local Network if iOS asked. Then tap Test Connection again. \
        Settings → Privacy & Security → Local Network → Library Survey.
        """
      default:
        break
      }
    }
    return error.localizedDescription
  }

  private func ensureSurvey(_ draft: SurveyDraft, backendURL: URL) async throws {
    struct Payload: Encodable {
      let surveyId: UUID
      let displayName: String
      let geography: SurveyGeography
    }
    var request = URLRequest(url: try Self.makeAPIURL(backendURL, path: "v1/surveys"))
    request.httpMethod = "POST"
    request.timeoutInterval = 30
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.setValue("create-\(draft.id.uuidString)", forHTTPHeaderField: "Idempotency-Key")
    request.httpBody = try JSONCoding.encoder(pretty: false).encode(
      Payload(surveyId: draft.id, displayName: draft.displayName, geography: draft.geography)
    )
    try await perform(request)
  }

  private func uploadFile(
    _ file: PackageFile,
    package: SealedSurveyPackage,
    backendURL: URL
  ) async throws {
    let fileURL = package.rootURL.appendingPathComponent(file.path)
    guard FileManager.default.fileExists(atPath: fileURL.path) else {
      throw URLError(.fileDoesNotExist)
    }
    var request = URLRequest(
      url: try Self.makeAPIURL(
        backendURL,
        path: "v1/surveys/\(package.surveyId.uuidString)/uploads",
        query: [URLQueryItem(name: "path", value: file.path)]
      )
    )
    request.httpMethod = "POST"
    request.timeoutInterval = 120
    request.setValue(file.mimeType, forHTTPHeaderField: "Content-Type")
    request.setValue("upload-\(file.path)-\(file.sha256)", forHTTPHeaderField: "Idempotency-Key")
    try await perform(request, fromFile: fileURL)
    await Task.yield()
  }

  private func seal(_ package: SealedSurveyPackage, backendURL: URL) async throws {
    var request = URLRequest(
      url: try Self.makeAPIURL(backendURL, path: "v1/surveys/\(package.surveyId.uuidString)/seal")
    )
    request.httpMethod = "POST"
    request.timeoutInterval = 60
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.setValue("seal-\(package.surveyId.uuidString)", forHTTPHeaderField: "Idempotency-Key")
    request.httpBody = try JSONCoding.encoder(pretty: false).encode(package.manifest)
    try await perform(request)
  }

  private func perform(_ request: URLRequest, fromFile fileURL: URL? = nil) async throws {
    let data: Data
    let response: URLResponse
    if let fileURL {
      (data, response) = try await URLSession.shared.upload(for: request, fromFile: fileURL)
    } else {
      (data, response) = try await URLSession.shared.data(for: request)
    }
    guard let http = response as? HTTPURLResponse else {
      throw URLError(.badServerResponse)
    }
    guard (200..<300).contains(http.statusCode) else {
      let body = String(data: data, encoding: .utf8)?
        .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
      let suffix = body.isEmpty ? "" : " — \(body)"
      throw NSError(
        domain: "LibrarySurvey.upload",
        code: http.statusCode,
        userInfo: [NSLocalizedDescriptionKey: "Server returned \(http.statusCode)\(suffix)"]
      )
    }
  }

  nonisolated private static func makeAPIURL(
    _ backendURL: URL,
    path: String,
    query: [URLQueryItem] = []
  ) throws -> URL {
    guard var components = URLComponents(url: backendURL, resolvingAgainstBaseURL: false) else {
      throw URLError(.badURL)
    }
    let trimmed = path.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
    components.path = "/" + trimmed
    components.queryItems = query.isEmpty ? nil : query
    guard let url = components.url else { throw URLError(.badURL) }
    return url
  }

  private func loadState(package: SealedSurveyPackage) -> UploadState {
    let url = package.rootURL.appendingPathComponent("upload-state.json")
    guard let data = try? Data(contentsOf: url),
          let state = try? JSONCoding.decoder().decode(UploadState.self, from: data)
    else { return UploadState(completedPaths: []) }
    return state
  }

  private func saveState(_ state: UploadState, package: SealedSurveyPackage) throws {
    try JSONCoding.encoder().encode(state).write(
      to: package.rootURL.appendingPathComponent("upload-state.json"),
      options: .atomic
    )
  }
}
