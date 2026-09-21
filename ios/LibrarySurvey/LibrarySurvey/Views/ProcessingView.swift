import SwiftUI

private struct ProcessingSurvey: Decodable {
  let status: String
  let packageHash: String?
}

private struct ProcessingEvent: Decodable, Identifiable {
  let sequence: Int
  let state: String
  let occurredAt: String
  let detail: String?
  var id: Int { sequence }
}

struct ProcessingView: View {
  let surveyId: UUID
  let backendURL: URL
  @State private var survey: ProcessingSurvey?
  @State private var events: [ProcessingEvent] = []
  @State private var message: String?

  var body: some View {
    List {
      Section("Current state") {
        Text(survey?.status.replacingOccurrences(of: "_", with: " ") ?? "Loading")
          .font(.headline)
        Text(survey?.packageHash == nil ? "Package not sealed" : "Package sealed")
        if let message { Text(message).foregroundStyle(.orange) }
      }
      Section("Processing timeline") {
        ForEach(events) { event in
          VStack(alignment: .leading, spacing: 4) {
            Text(event.state.replacingOccurrences(of: "_", with: " "))
              .font(.headline)
            Text(event.detail ?? event.occurredAt).font(.caption)
          }
        }
      }
    }
    .navigationTitle("Processing")
    .task { await refresh() }
    .refreshable { await refresh() }
  }

  private func refresh() async {
    do {
      let root = backendURL.appendingPathComponent("v1/surveys/\(surveyId.uuidString)")
      let (surveyData, surveyResponse) = try await OperatorSession.data(from: root)
      let (eventsData, eventsResponse) = try await OperatorSession.data(
        from: root.appendingPathComponent("jobs")
      )
      guard (surveyResponse as? HTTPURLResponse)?.statusCode == 200,
            (eventsResponse as? HTTPURLResponse)?.statusCode == 200 else {
        throw URLError(.badServerResponse)
      }
      survey = try JSONCoding.decoder().decode(ProcessingSurvey.self, from: surveyData)
      events = try JSONCoding.decoder().decode([ProcessingEvent].self, from: eventsData)
      message = nil
    } catch { message = error.localizedDescription }
  }
}
