import SwiftUI

struct InventoryRowDetailView: View {
  let surveyId: UUID
  let row: Stage4Row
  let backendURL: URL

  var body: some View {
    List {
      Section("Row status") {
        Text("Detected / actual: \(row.detectedCount) / \(row.actualCount.map(String.init) ?? "unknown")")
        Text("Readable coverage: \(row.coverage.map { String(format: "%.0f%%", $0 * 100) } ?? "unknown")")
        AccessibleStatusLabel(
          text: "Count status: \(row.coverageStatus ?? "unknown")",
          kind: row.recapture ? .warning : .success
        )
        if let interval = row.countInterval, row.recapture {
          Text("Possible count: \(Int(interval.low))–\(Int(interval.high))")
        }
        if row.recapture {
          AccessibleStatusLabel(
            text: "Recapture this named row before accepting the count",
            kind: .warning
          )
        }
        if row.placement != "operator" {
          Text("Unregistered overlay. Shelf location on the plan is not measured.")
            .font(.footnote)
        }
      }
      Section("Selectable spine slots") {
        Text("Slots are ordered along the shelf face. Each outlined slot opens its own physical copy and evidence. These are slot markers, not image bounding boxes.")
          .font(.footnote)
        ScrollView(.horizontal) {
          HStack(alignment: .bottom, spacing: 8) {
            ForEach(row.copies) { copy in
              NavigationLink {
                PriceEvidenceView(surveyId: surveyId, copy: copy, backendURL: backendURL)
              } label: {
                VStack {
                  Text(copy.slot.map(String.init) ?? "?")
                    .font(.headline.monospacedDigit())
                  Text(copy.identityStatus == "unresolved" ? "?" : "✓")
                    .font(.caption)
                }
                .frame(width: 52, height: 96)
                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(.primary, lineWidth: 2))
              }
              .minimumScaledTouchTarget()
              .accessibilityLabel("Slot \(copy.slot.map(String.init) ?? "unknown"), \(copy.title ?? copy.label ?? "unidentified book"), \(copy.valuationStatus)")
              .accessibilityHint("Opens evidence and valuation details for this physical copy")
            }
          }
          .padding(.vertical, 8)
        }
      }
      Section("Copies") {
        ForEach(row.copies) { copy in
          NavigationLink {
            PriceEvidenceView(surveyId: surveyId, copy: copy, backendURL: backendURL)
          } label: {
            VStack(alignment: .leading, spacing: 4) {
              Text("Slot \(copy.slot.map(String.init) ?? "?") · \(copy.title ?? copy.label ?? "Unidentified book")")
                .font(.headline)
              Text("Identity: \(copy.identityStatus) · Condition: \(copy.condition ?? "unreviewed")")
              Text("Search: \(copy.query == nil ? "waiting for identity" : copy.draftCount > 0 ? "drafts to review" : "ready or pending")")
              Text("Value: \(copy.valuationStatus) · \(copy.reason)")
                .foregroundStyle(.secondary)
            }
            .font(.footnote)
          }
          .accessibilityElement(children: .combine)
        }
      }
    }
    .navigationTitle("\(row.faceId ?? "Face") / \(row.rowId ?? "Row")")
  }
}
