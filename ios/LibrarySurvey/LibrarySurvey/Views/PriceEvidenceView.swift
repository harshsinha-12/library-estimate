import SwiftUI

struct PriceEvidenceView: View {
  let surveyId: UUID
  let copy: Stage4Copy
  let backendURL: URL
  @State private var result: Stage4SearchResponse?
  @State private var message: String?
  @State private var manualAmount = ""
  @State private var manualReason = ""

  var body: some View {
    Form {
      Section("Copy") {
        Text(copy.title ?? copy.label ?? copy.assetCopyId)
        Text("Status: \(copy.valuationStatus.replacingOccurrences(of: "_", with: " "))")
        Text(copy.reason)
        if let isbn = copy.isbn { Text("ISBN \(isbn)") }
        if let task = copy.identityTask {
          Text("Pass C: \(task)").foregroundStyle(.orange)
        }
        if copy.excluded {
          Text("Counted and excluded from valuation.")
        }
      }
      if let search = result?.search {
        Section("Price search") {
          Text(search.query)
          if let listing = URL(string: search.listingUrl), !search.listingUrl.isEmpty {
            Link("Open listing", destination: listing)
          }
          if let parser = search.parser {
            Text("Parser: \(parser)").font(.caption)
          }
        }
        Section("Citations") {
          ForEach(search.citations) { citation in
            VStack(alignment: .leading, spacing: 4) {
              Text(citation.title).font(.headline)
              Text(citation.snippet).font(.footnote)
              Text("\(citation.offerType) \(citation.parsedAmount ?? "no amount")")
                .font(.caption)
              Link(citation.url, destination: URL(string: citation.url) ?? backendURL)
                .font(.caption2)
            }
          }
        }
      }
      Section("Drafts — confirm a physical offer") {
        ForEach(result?.drafts ?? []) { draft in
          VStack(alignment: .leading, spacing: 6) {
            Text(draft.title ?? "Draft observation")
            Text(draft.snippet ?? "").font(.footnote)
            Text("\(draft.offerType) \(draft.parsedAmount ?? "") \(draft.currency ?? "")")
            HStack {
              Button("Confirm physical price") { Task { await decide(action: "confirm", draft: draft) } }
                .disabled(draft.offerType != "physical" || draft.parsedAmount == nil)
              Button("Reject") { Task { await decide(action: "reject", draft: draft) } }
            }
          }
        }
        if (result?.drafts ?? []).isEmpty {
          Text("Search to load draft citations. They are not prices until confirmed.")
            .font(.footnote)
        }
      }
      Section("Manual replacement evidence") {
        TextField("Amount", text: $manualAmount)
          .keyboardType(.decimalPad)
        TextField("Reason", text: $manualReason, axis: .vertical)
        Button("Save manual price") { Task { await decide(action: "manual", draft: nil) } }
          .disabled(manualAmount.isEmpty || manualReason.isEmpty || copy.excluded)
        Button("No local comparable") { Task { await decide(action: "no_comparable", draft: nil) } }
          .disabled(copy.excluded)
      }
      Section {
        Button("Search prices for this copy") { Task { await search() } }
          .disabled(!copy.eligible || copy.query == nil)
      }
      if let message { Text(message).foregroundStyle(.orange) }
    }
    .navigationTitle("Price evidence")
    .task { if copy.eligible, copy.query != nil { await search() } }
  }

  private func search() async {
    do {
      var request = URLRequest(
        url: backendURL.appendingPathComponent("v1/assets/\(copy.assetCopyId)/price-search")
      )
      request.httpMethod = "POST"
      request.setValue("application/json", forHTTPHeaderField: "Content-Type")
      request.httpBody = try JSONCoding.encoder(pretty: false).encode(["survey_id": surveyId.uuidString])
      let (data, response) = try await URLSession.shared.data(for: request)
      guard (response as? HTTPURLResponse)?.statusCode == 200 else {
        throw URLError(.badServerResponse)
      }
      result = try JSONCoding.decoder().decode(Stage4SearchResponse.self, from: data)
      message = result?.reason
    } catch { message = error.localizedDescription }
  }

  private func decide(action: String, draft: Stage4Draft?) async {
    do {
      struct Body: Encodable {
        let surveyId: UUID
        let action: String
        let priceObservationId: String?
        let amount: Double?
        let reason: String?
      }
      var request = URLRequest(
        url: backendURL.appendingPathComponent("v1/assets/\(copy.assetCopyId)/price-observations")
      )
      request.httpMethod = "POST"
      request.setValue("application/json", forHTTPHeaderField: "Content-Type")
      request.httpBody = try JSONCoding.encoder(pretty: false).encode(
        Body(
          surveyId: surveyId,
          action: action,
          priceObservationId: draft?.priceObservationId,
          amount: action == "manual" ? Double(manualAmount) : nil,
          reason: action == "manual" || action == "no_comparable" ? manualReason : nil
        )
      )
      let (_, response) = try await URLSession.shared.data(for: request)
      guard (response as? HTTPURLResponse)?.statusCode == 200 else {
        throw URLError(.badServerResponse)
      }
      message = "Saved. Drafts are not shown as confirmed prices until this step."
      await search()
    } catch { message = error.localizedDescription }
  }
}
