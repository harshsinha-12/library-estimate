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

private struct OperatorAction: Decodable, Identifiable {
  var id: String { code }
  let code: String
  let failure: String
  let status: String
  let nextAction: String
}

private struct OperatorActionsResponse: Decodable {
  let actions: [OperatorAction]
}

struct ProcessingView: View {
  let surveyId: UUID
  let backendURL: URL
  @State private var survey: ProcessingSurvey?
  @State private var events: [ProcessingEvent] = []
  @State private var actions: [OperatorAction] = []
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
      Section("Failures and next actions") {
        Text("Action required means this survey has a matching recorded signal. Not observed does not prove a failure was impossible.")
          .font(.footnote)
        ForEach(actions.filter { $0.status == "action_required" }) { action in
          actionRow(action)
        }
        if !actions.contains(where: { $0.status == "action_required" }) {
          Text("No recorded failure currently needs an action.")
        }
      }
      Section("Failure policy reference") {
        ForEach(actions.filter { $0.status != "action_required" }) { action in
          actionRow(action)
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
      let (actionsData, actionsResponse) = try await OperatorSession.data(
        from: root.appendingPathComponent("operator-actions")
      )
      guard (surveyResponse as? HTTPURLResponse)?.statusCode == 200,
            (eventsResponse as? HTTPURLResponse)?.statusCode == 200,
            (actionsResponse as? HTTPURLResponse)?.statusCode == 200 else {
        throw URLError(.badServerResponse)
      }
      survey = try JSONCoding.decoder().decode(ProcessingSurvey.self, from: surveyData)
      events = try JSONCoding.decoder().decode([ProcessingEvent].self, from: eventsData)
      actions = try JSONCoding.decoder().decode(OperatorActionsResponse.self, from: actionsData).actions
      message = nil
    } catch { message = error.localizedDescription }
  }

  private func actionRow(_ action: OperatorAction) -> some View {
    VStack(alignment: .leading, spacing: 4) {
      Text(action.failure).font(.headline)
      Text("Status: \(action.status.replacingOccurrences(of: "_", with: " "))")
        .font(.caption)
      Text(action.nextAction).font(.footnote)
    }
  }
}
