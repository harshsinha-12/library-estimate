import SwiftUI

private struct ReviewAsset: Decodable, Identifiable {
  let assetCopyId: String
  let category: String
  let label: String?
  var id: String { assetCopyId }
}

private struct ReviewItem: Decodable, Identifiable {
  let id: String
  let kind: String
  let assetCopyId: String?
  let message: String
  let status: String
}

private struct ReviewResponse: Decodable {
  let assets: [ReviewAsset]
  let queue: [ReviewItem]
}

struct Stage3ReviewView: View {
  let surveyId: UUID
  let backendURL: URL
  @State private var review: ReviewResponse?
  @State private var selectedAssetId = ""
  @State private var message: String?

  var body: some View {
    List {
      if let review {
        Section("Unresolved queue") {
          ForEach(review.queue.filter { $0.status == "open" }) { item in
            VStack(alignment: .leading, spacing: 8) {
              Text(item.kind.replacingOccurrences(of: "_", with: " ").capitalized)
                .font(.headline)
              Text(item.message)
              if let asset = item.assetCopyId { Text("Asset: \(asset)").font(.caption) }
              if item.kind == "unbound_note" {
                Picker("Bind to", selection: $selectedAssetId) {
                  Text("Choose an asset").tag("")
                  ForEach(review.assets) { asset in
                    Text(asset.label ?? "\(asset.category) \(asset.assetCopyId)")
                      .tag(asset.assetCopyId)
                  }
                }
                Button("Bind note") { Task { await decide(item, action: "bind_note") } }
                  .disabled(selectedAssetId.isEmpty)
              }
              HStack {
                if item.kind == "rescan_barcode" {
                  Button("Rescan barcode") { Task { await decide(item, action: "rescan_barcode") } }
                }
                Button("Keep unresolved") { Task { await decide(item, action: "keep_unresolved") } }
              }
            }
          }
          if !review.queue.contains(where: { $0.status == "open" }) {
            Text("No open Stage 3 exceptions")
          }
        }
        Section("Counted assets") {
          Text("\(review.assets.count) physical objects, including excluded objects")
        }
      } else {
        ProgressView("Loading review queue")
      }
      if let message { Text(message).foregroundStyle(.orange) }
    }
    .navigationTitle("Stage 3 Review")
    .task { await load() }
  }

  private func load() async {
    do {
      let url = backendURL.appendingPathComponent("v1/surveys/\(surveyId.uuidString)/review")
      let (data, response) = try await OperatorSession.data(from: url)
      guard (response as? HTTPURLResponse)?.statusCode == 200 else {
        throw URLError(.badServerResponse)
      }
      review = try JSONCoding.decoder().decode(ReviewResponse.self, from: data)
      message = nil
    } catch { message = error.localizedDescription }
  }

  private func decide(_ item: ReviewItem, action: String) async {
    do {
      struct Decision: Encodable {
        let surveyId: UUID
        let action: String
        let assetCopyId: String?
      }
      let url = backendURL.appendingPathComponent("v1/reviews/\(item.id)/decision")
      var request = URLRequest(url: url)
      request.httpMethod = "POST"
      request.setValue("application/json", forHTTPHeaderField: "Content-Type")
      request.httpBody = try JSONCoding.encoder(pretty: false).encode(
        Decision(surveyId: surveyId, action: action,
                 assetCopyId: action == "bind_note" ? selectedAssetId : nil)
      )
      let (_, response) = try await OperatorSession.data(for: request)
      guard (response as? HTTPURLResponse)?.statusCode == 200 else {
        throw URLError(.badServerResponse)
      }
      await load()
    } catch { message = error.localizedDescription }
  }
}
