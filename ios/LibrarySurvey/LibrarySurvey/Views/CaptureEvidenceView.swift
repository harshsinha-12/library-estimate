import SwiftUI

struct CaptureEvidenceView: View {
  let surveyId: UUID
  let path: String
  let backendURL: URL
  @State private var image: UIImage?
  @State private var message: String?

  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 12) {
        Text(path).font(.caption).textSelection(.enabled)
        if let image {
          Image(uiImage: image)
            .resizable()
            .scaledToFit()
            .accessibilityLabel("Capture evidence at \(path)")
            .accessibilityHint("Static evidence image. It is not a live camera view.")
        } else if let message {
          AccessibleStatusLabel(text: message, kind: .error)
        } else {
          ProgressView("Loading evidence")
        }
      }
      .frame(maxWidth: .infinity, alignment: .leading)
      .padding()
    }
    .navigationTitle("Evidence")
    .task { await load() }
    .accessibilityStatusAnnouncements(accessibilityStatus)
  }

  private var accessibilityStatus: String {
    if image != nil { return "Evidence image loaded" }
    if let message { return "Evidence could not be displayed. \(message)" }
    return "Loading evidence image"
  }

  private func load() async {
    do {
      let root = backendURL.appendingPathComponent("v1/surveys/\(surveyId.uuidString)/evidence")
      guard var components = URLComponents(url: root, resolvingAgainstBaseURL: false) else {
        throw URLError(.badURL)
      }
      components.queryItems = [URLQueryItem(name: "path", value: path)]
      guard let url = components.url else { throw URLError(.badURL) }
      let (data, response) = try await OperatorSession.data(from: url)
      guard (response as? HTTPURLResponse)?.statusCode == 200 else {
        throw URLError(.badServerResponse)
      }
      guard let decoded = UIImage(data: data) else {
        message = "This evidence is not an image."
        return
      }
      image = decoded
    } catch { message = error.localizedDescription }
  }
}
