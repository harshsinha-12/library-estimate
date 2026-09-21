import SwiftUI

struct OverviewView: View {
  let surveyId: UUID
  let backendURL: URL
  @State private var overview: Stage4Overview?
  @State private var message: String?

  var body: some View {
    List {
      if let overview {
        Section("Survey") {
          Text(overview.displayName)
          Text(overview.cityMarket)
        }
        Section("Counts") {
          Text("Physical copies: \(overview.copyCount)")
          Text("Editions: \(overview.editionCount)")
          Text("Priced / eligible: \(overview.pricedEligible.numerator)/\(overview.pricedEligible.denominator ?? 0)")
          Text("Unresolved: \(overview.unresolvedCount)")
        }
        Section("Contents range") {
          Text(overview.contents.status.replacingOccurrences(of: "_", with: " ").capitalized)
          if let low = overview.contents.low, let high = overview.contents.high,
             let central = overview.contents.central {
            Text("\(overview.contents.currency) \(low, specifier: "%.2f") – \(central, specifier: "%.2f") – \(high, specifier: "%.2f")")
          } else {
            Text("Unresolved books stay visible and unpriced.")
          }
          if let note = overview.contents.note { Text(note).font(.footnote) }
        }
        Section("Building reconstruction") {
          Text("Basis: \(overview.building.basis.replacingOccurrences(of: "_", with: " "))")
          if let version = overview.building.rateTableVersion {
            Text("Rate table: \(version)")
          }
          if let area = overview.building.floorArea?.value {
            Text("Floor area: \(area, specifier: "%.1f") m²")
          }
          if let amount = overview.building.amount {
            Text("\(amount.unit ?? overview.building.currency ?? "") \(amount.value, specifier: "%.0f")")
            if let interval = amount.interval {
              Text("Range \(interval.low, specifier: "%.0f") – \(interval.high, specifier: "%.0f")")
            }
          }
          Text("This is replacement cost, not a sale price.")
            .font(.footnote)
          if let disclaimer = overview.building.disclaimer {
            Text(disclaimer).font(.caption).foregroundStyle(.secondary)
          }
        }
        if !overview.recapture.isEmpty {
          Section("Recapture") {
            ForEach(overview.recapture, id: \.self, content: Text.init)
          }
        }
        Section("Price searches this run") {
          if overview.ledger.lines.isEmpty {
            Text("No price searches yet for this survey.")
              .font(.footnote)
          }
          ForEach(overview.ledger.lines.suffix(8).reversed()) { line in
            Text("\(line.kind) · \(line.cached ? "cache" : "live") · \(line.query)")
              .font(.caption)
          }
        }
      } else {
        ProgressView("Loading overview")
      }
      if let message { Text(message).foregroundStyle(.orange) }
    }
    .navigationTitle("Overview")
    .task { await load() }
    .refreshable { await load() }
  }

  private func load() async {
    do {
      let url = backendURL.appendingPathComponent("v1/surveys/\(surveyId.uuidString)/overview")
      let (data, response) = try await URLSession.shared.data(from: url)
      guard (response as? HTTPURLResponse)?.statusCode == 200 else {
        throw URLError(.badServerResponse)
      }
      overview = try JSONCoding.decoder().decode(Stage4Overview.self, from: data)
      message = nil
    } catch { message = error.localizedDescription }
  }
}
