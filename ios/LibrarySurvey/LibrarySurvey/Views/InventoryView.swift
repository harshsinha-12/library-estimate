import SwiftUI

struct InventoryView: View {
  let surveyId: UUID
  let backendURL: URL
  @State private var overview: Stage4Overview?
  @State private var message: String?

  var body: some View {
    List {
      if let overview {
        Section("Row roster") {
          Button("Search prices for every found edition") { Task { await queue() } }
          Text("Detected/actual and priced/eligible stay visible. Drafts are not confirmed prices. After seal, Fable and Astra run on every copy automatically. Open a copy to see the Jev comparison; the button there is optional replay.")
            .font(.footnote)
        }
        ForEach(overview.rows) { row in
          Section(rowTitle(row)) {
            if row.recapture {
              Text("Partial coverage. Recapture this named row.")
                .foregroundStyle(.orange)
            }
            ForEach(row.copies) { copy in
              NavigationLink {
                PriceEvidenceView(surveyId: surveyId, copy: copy, backendURL: backendURL)
              } label: {
                VStack(alignment: .leading, spacing: 4) {
                  Text(copy.title ?? copy.label ?? copy.assetCopyId)
                    .font(.headline)
                  Text(copyLine(copy))
                    .font(.caption)
                  Text(copy.reason)
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                }
              }
            }
            if row.copies.isEmpty {
              Text("No copies on this row yet.")
            }
          }
        }
        Section("Other objects") {
          ForEach(overview.copies.filter { $0.faceId == nil }) { copy in
            NavigationLink {
              PriceEvidenceView(surveyId: surveyId, copy: copy, backendURL: backendURL)
            } label: {
              Text("\(copy.label ?? copy.category) · \(copy.valuationStatus)")
            }
          }
        }
      } else {
        ProgressView("Loading inventory")
      }
      if let message { Text(message).foregroundStyle(.orange) }
    }
    .navigationTitle("Inventory")
    .task { await load() }
    .refreshable { await load() }
  }

  private func rowTitle(_ row: Stage4Row) -> String {
    let detected = row.detectedCount
    let actual = row.actualCount.map(String.init) ?? "?"
    return "\(row.faceId ?? "face") \(row.rowId ?? "row") · \(detected)/\(actual)"
  }

  private func copyLine(_ copy: Stage4Copy) -> String {
    let slot = copy.slot.map { "slot \($0)" } ?? copy.category
    let isbn = copy.isbn.map { "ISBN \($0)" } ?? copy.identityStatus
    return "\(slot) · \(isbn) · \(copy.valuationStatus)"
  }

  private func load() async {
    do {
      let url = backendURL.appendingPathComponent("v1/surveys/\(surveyId.uuidString)/overview")
      let (data, response) = try await OperatorSession.data(from: url)
      guard (response as? HTTPURLResponse)?.statusCode == 200 else {
        throw URLError(.badServerResponse)
      }
      overview = try JSONCoding.decoder().decode(Stage4Overview.self, from: data)
      message = nil
    } catch { message = error.localizedDescription }
  }

  private func queue() async {
    do {
      var request = URLRequest(
        url: backendURL.appendingPathComponent("v1/surveys/\(surveyId.uuidString)/price-search-queue")
      )
      request.httpMethod = "POST"
      request.setValue("application/json", forHTTPHeaderField: "Content-Type")
      request.httpBody = Data("{}".utf8)
      let (_, response) = try await OperatorSession.data(for: request)
      guard (response as? HTTPURLResponse)?.statusCode == 200 else {
        throw URLError(.badServerResponse)
      }
      await load()
    } catch { message = error.localizedDescription }
  }
}
