import SwiftUI

private struct ReportSpend: Decodable {
  let estimatedCostUsd: String
  let unpricedCalls: Int
}

private struct SurveyReport: Decodable {
  let surveyId: UUID
  let generatedAt: String
  let packageHash: String
  let status: String
  let limitations: [String]
  let spend: ReportSpend
  let modelPipelines: ModelPipelines?
  let modelRuns: [ModelReplayRun]?
}

struct ReportView: View {
  let surveyId: UUID
  let backendURL: URL
  @State private var report: SurveyReport?
  @State private var pdfURL: URL?
  @State private var message: String?

  var body: some View {
    List {
      if let report {
        Section("Report") {
          AccessibleStatusLabel(
            text: "Status: \(report.status)",
            kind: report.status.localizedCaseInsensitiveContains("fail") ? .error : .success
          )
          Text("Generated: \(report.generatedAt)")
          Text("Package SHA-256: \(report.packageHash)")
            .font(.caption)
            .textSelection(.enabled)
          Text("Estimated provider spend: $\(report.spend.estimatedCostUsd)")
          if report.spend.unpricedCalls > 0 {
            AccessibleStatusLabel(
              text: "\(report.spend.unpricedCalls) provider calls lack a price",
              kind: .warning
            )
          }
        }
        Section("Fable, Astra, and Jev") {
          Text("After seal, Fable (A) and Astra replay (B) run on every copy automatically. The inventory button is optional replay of the same sealed bytes. Astra-live during capture is assist only, not Pipeline B.")
            .font(.footnote)
          if let pipelines = report.modelPipelines {
            Text("Fable: \(pipelines.fable?.status ?? "not_run")")
            Text("Astra: \(pipelines.astra?.status ?? "not_run")")
            Text("Jev: \(pipelines.jev?.status ?? "not_run")")
            if let live = pipelines.astraLive {
              Text("Astra-live assist: \(live.status)")
            }
            if let note = pipelines.note { Text(note).font(.caption) }
          }
          if let runs = report.modelRuns, !runs.isEmpty {
            ForEach(runs) { run in
              ModelReplayResultBlock(run: run, title: run.assetCopyId)
            }
          } else {
            Text("No model runs stored yet.")
              .foregroundStyle(.secondary)
          }
        }
        Section("Limitations") {
          ForEach(report.limitations, id: \.self) { Text($0) }
        }
        Section("Export") {
          if let pdfURL {
            ShareLink(item: pdfURL) {
              Label("Share PDF report", systemImage: "square.and.arrow.up")
            }
            .minimumScaledTouchTarget()
          } else {
            Button("Prepare PDF") { Task { await loadPDF() } }
              .minimumScaledTouchTarget()
          }
        }
      } else {
        ProgressView("Loading report")
      }
      if let message { AccessibleStatusLabel(text: message, kind: .error) }
    }
    .navigationTitle("Report")
    .task { await load() }
    .refreshable { await load() }
    .accessibilityStatusAnnouncements(accessibilityStatus)
  }

  private var accessibilityStatus: String {
    if let message { return "Report error. \(message)" }
    if pdfURL != nil { return "PDF report is ready to share" }
    return report.map { "Report loaded with status \($0.status)" } ?? "Loading report"
  }

  private func load() async {
    do {
      let url = backendURL.appendingPathComponent("v1/surveys/\(surveyId.uuidString)/report")
      let (data, response) = try await OperatorSession.data(from: url)
      guard (response as? HTTPURLResponse)?.statusCode == 200 else {
        throw URLError(.badServerResponse)
      }
      report = try JSONCoding.decoder().decode(SurveyReport.self, from: data)
      message = nil
    } catch { message = error.localizedDescription }
  }

  private func loadPDF() async {
    do {
      let url = backendURL.appendingPathComponent("v1/surveys/\(surveyId.uuidString)/report.pdf")
      let (data, response) = try await OperatorSession.data(from: url)
      guard (response as? HTTPURLResponse)?.statusCode == 200,
            data.starts(with: Data("%PDF".utf8)) else {
        throw URLError(.badServerResponse)
      }
      let destination = FileManager.default.temporaryDirectory
        .appendingPathComponent("library-survey-\(surveyId.uuidString).pdf")
      try data.write(to: destination, options: .atomic)
      pdfURL = destination
      message = nil
    } catch { message = error.localizedDescription }
  }
}
