import SwiftUI

struct PriceEvidenceView: View {
  let surveyId: UUID
  let copy: Stage4Copy
  let backendURL: URL
  @State private var result: Stage4SearchResponse?
  @State private var replay: ModelReplayRun?
  @State private var replaying = false
  @State private var message: String?
  @State private var manualAmount = ""
  @State private var manualReason = ""
  @State private var correctedTitle = ""
  @State private var correctedAuthor = ""
  @State private var correctedISBN = ""
  @State private var correctionReason = ""
  @State private var correctionScope = "volume"
  @State private var correctionFormat = "unknown"

  var body: some View {
    Form {
      Section("Copy") {
        Text(copy.title ?? copy.label ?? copy.assetCopyId)
        AccessibleStatusLabel(
          text: "Status: \(copy.valuationStatus.replacingOccurrences(of: "_", with: " "))",
          kind: copy.valuationStatus == "quoted" || copy.valuationStatus == "manual" ? .success : .warning
        )
        Text(copy.reason)
        Text("Condition: \(copy.condition ?? "unreviewed")")
        Text("Search: \(copy.query == nil ? "waiting for identity" : copy.draftCount > 0 ? "drafts need review" : "ready or pending")")
        if let amount = copy.valuation?.amount, ["quoted", "manual"].contains(copy.valuationStatus) {
          Text("Reviewed physical range: \(amount.unit ?? "") \(amount.interval?.low ?? amount.value, specifier: "%.2f")–\(amount.interval?.high ?? amount.value, specifier: "%.2f")")
        }
        if let isbn = copy.isbn { Text("ISBN \(isbn)") }
        if let task = copy.identityTask {
          AccessibleStatusLabel(text: "Pass C: \(task)", kind: .warning)
        }
        if copy.excluded {
          Text("Counted and excluded from valuation.")
        }
      }
      Section("Count and geometry evidence") {
        if copy.placement != "operator", copy.faceId != nil {
          Text("Unregistered overlay: this shelf position is not measured on the RoomPlan plan.")
            .font(.footnote)
        }
        ForEach(copy.countEvidence ?? []) { observation in
          VStack(alignment: .leading, spacing: 5) {
            Text("\(observation.faceId ?? "face") / \(observation.rowId ?? "row") / slot \(observation.slot.map(String.init) ?? "?")")
              .font(.headline)
            Text("Shelf-face x: \(observation.x.map { String(format: "%.3f", $0) } ?? "unknown") · readable: \(observation.readable == true ? "yes" : "no")")
              .font(.caption)
            if observation.stacked == true || observation.leaning == true {
              Text("Stacked: \(observation.stacked == true ? "yes" : "no") · leaning: \(observation.leaning == true ? "yes" : "no")")
                .font(.caption)
            }
            evidenceLink(observation.evidenceRef, title: "Open count crop")
          }
        }
        if (copy.countEvidence ?? []).isEmpty {
          Text("No count observation is stored for this copy.")
        }
      }
      if !(copy.damageEvidence ?? []).isEmpty || !(copy.spokenNotes ?? []).isEmpty {
        Section("Damage and spoken evidence") {
          ForEach(copy.spokenNotes ?? []) { note in
            VStack(alignment: .leading) {
              Text(note.text)
              Text("Spoken note · \(note.status ?? "unknown") · \(note.associationMethod ?? "unknown binding")")
                .font(.caption)
              evidenceLink(note.closeupRef, title: "Open linked close-up")
            }
          }
          ForEach(copy.damageEvidence ?? []) { damage in
            VStack(alignment: .leading) {
              Text("\(damage.type) · \(damage.severityCandidate ?? "severity unknown") · \(damage.region ?? "region unknown")")
              Text(damage.status ?? "needs review").font(.caption)
              evidenceLink(damage.closeupRef, title: "Open damage close-up")
              evidenceLink(damage.scaleRef, title: "Open scale reference")
            }
          }
        }
      }
      if !(copy.reviewTasks ?? []).isEmpty {
        Section("Corrections and barcode rescan") {
          ForEach(copy.reviewTasks ?? []) { task in
            Text("\(task.kind.replacingOccurrences(of: "_", with: " ")) · \(task.message) · \(task.status)")
          }
          NavigationLink("Open review and rescan queue") {
            Stage3ReviewView(surveyId: surveyId, backendURL: backendURL)
          }
        }
      }
      if copy.category == "book" {
        Section("Correct this book") {
          Text("A correction stays on this physical copy. A typed ISBN is checksum checked and held for catalog review before ISBN price search.")
            .font(.footnote)
          TextField("Visible title", text: $correctedTitle)
          TextField("Author", text: $correctedAuthor)
          TextField("ISBN, if visible", text: $correctedISBN)
            .keyboardType(.numbersAndPunctuation)
          Picker("Identifier scope", selection: $correctionScope) {
            Text("Volume").tag("volume")
            Text("Set").tag("set")
          }
          Picker("Format", selection: $correctionFormat) {
            Text("Unknown").tag("unknown")
            Text("Paperback").tag("paperback")
            Text("Hardcover").tag("hardcover")
            Text("Library binding").tag("library_binding")
          }
          TextField("Why is this correction needed?", text: $correctionReason, axis: .vertical)
          Button("Save identity correction") { Task { await correctIdentity() } }
            .disabled(correctedTitle.trimmingCharacters(in: .whitespaces).count < 2 ||
                      correctionReason.trimmingCharacters(in: .whitespaces).count < 3)
        }
      }
      if !(copy.confirmedPriceEvidence ?? []).isEmpty {
        Section("Reviewed value evidence") {
          ForEach(copy.confirmedPriceEvidence ?? []) { evidence in
            VStack(alignment: .leading, spacing: 4) {
              Text("\(evidence.currency ?? "") \(evidence.parsedAmount ?? "unknown") · confirmed physical")
              Text(evidence.title ?? evidence.snippet ?? "Manual evidence").font(.footnote)
              if let url = evidence.sourceUrl.flatMap(URL.init(string:)), ["http", "https"].contains(url.scheme ?? "") {
                Link("Open source listing", destination: url)
              }
              if let hash = evidence.evidenceHash {
                Text("Evidence SHA-256: \(hash)").font(.caption2).textSelection(.enabled)
              }
            }
          }
        }
      }
      if let paths = copy.evidencePaths, !paths.isEmpty {
        Section("Capture evidence") {
          ForEach(paths, id: \.self) { path in
            NavigationLink(path) {
              CaptureEvidenceView(surveyId: surveyId, path: path, backendURL: backendURL)
            }
          }
        }
      }
      Section("Fable, Astra, and Jev") {
        Text("After seal, Fable (A) and Astra replay (B) run on every copy automatically, independently, on the same evidence bytes. This button is optional replay. Jev scores A vs B and does not write count or price. Results for this named copy appear here after the run. The same run is also listed under Report. These models are not the price search.")
          .font(.footnote)
        if replaying {
          ProgressView("Running Fable, then Astra, then Jev")
        } else {
          Button("Run Fable, Astra, and Jev") { Task { await runReplay() } }
            .minimumScaledTouchTarget()
        }
        if let replay {
          ModelReplayResultBlock(run: replay, title: copy.title ?? copy.label)
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
                .minimumScaledTouchTarget()
              Button("Reject") { Task { await decide(action: "reject", draft: draft) } }
                .minimumScaledTouchTarget()
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
      if let message { AccessibleStatusLabel(text: message, kind: .neutral) }
    }
    .navigationTitle("Price evidence")
    .task {
      correctedTitle = copy.title ?? ""
      await loadReplay()
      if copy.eligible, copy.query != nil { await search() }
    }
    .accessibilityStatusAnnouncements(accessibilityStatus)
  }

  private var accessibilityStatus: String {
    if replaying { return "Running Fable, Astra, and Jev assessments" }
    if let message { return message }
    if replay != nil { return "Model assessment results loaded" }
    return "Price evidence loaded for \(copy.title ?? copy.label ?? "this copy")"
  }

  private func correctIdentity() async {
    do {
      struct Body: Encodable {
        let title: String
        let author: String
        let isbn: String
        let reason: String
        let scope: String
        let format: String
      }
      var request = URLRequest(url: backendURL.appendingPathComponent(
        "v1/surveys/\(surveyId.uuidString)/books/\(copy.assetCopyId)/identity-correction"
      ))
      request.httpMethod = "POST"
      request.setValue("application/json", forHTTPHeaderField: "Content-Type")
      request.httpBody = try JSONCoding.encoder(pretty: false).encode(Body(
        title: correctedTitle, author: correctedAuthor, isbn: correctedISBN,
        reason: correctionReason, scope: correctionScope, format: correctionFormat
      ))
      let (data, response) = try await OperatorSession.data(for: request)
      guard (response as? HTTPURLResponse)?.statusCode == 200 else {
        message = httpDetail(data) ?? "Correction was not saved"
        return
      }
      message = "Correction saved on this copy. Reopen Inventory to see the updated identity. Typed ISBN awaits catalog review."
    } catch { message = error.localizedDescription }
  }

  @ViewBuilder
  private func evidenceLink(_ path: String?, title: String) -> some View {
    if let path, path.contains("/") {
      NavigationLink(title) {
        CaptureEvidenceView(surveyId: surveyId, path: path, backendURL: backendURL)
      }
    }
  }

  private func search() async {
    do {
      var request = URLRequest(
        url: backendURL.appendingPathComponent("v1/assets/\(copy.assetCopyId)/price-search")
      )
      request.httpMethod = "POST"
      request.setValue("application/json", forHTTPHeaderField: "Content-Type")
      request.httpBody = try JSONCoding.encoder(pretty: false).encode(["survey_id": surveyId.uuidString])
      let (data, response) = try await OperatorSession.data(for: request)
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
      let (_, response) = try await OperatorSession.data(for: request)
      guard (response as? HTTPURLResponse)?.statusCode == 200 else {
        throw URLError(.badServerResponse)
      }
      message = "Saved. Drafts are not shown as confirmed prices until this step."
      await search()
    } catch { message = error.localizedDescription }
  }

  private func loadReplay() async {
    do {
      let url = backendURL.appendingPathComponent("v1/surveys/\(surveyId.uuidString)/model-runs")
      let (data, response) = try await OperatorSession.data(from: url)
      guard (response as? HTTPURLResponse)?.statusCode == 200 else {
        throw URLError(.badServerResponse)
      }
      let list = try JSONCoding.decoder().decode(ModelReplayList.self, from: data)
      replay = list.runs
        .filter { $0.assetCopyId == copy.assetCopyId }
        .sorted { ($0.createdAt ?? "") < ($1.createdAt ?? "") }
        .last
    } catch { message = error.localizedDescription }
  }

  private func runReplay() async {
    replaying = true
    defer { replaying = false }
    do {
      var request = URLRequest(
        url: backendURL.appendingPathComponent(
          "v1/surveys/\(surveyId.uuidString)/assets/\(copy.assetCopyId)/model-replay"
        )
      )
      request.httpMethod = "POST"
      request.timeoutInterval = 180
      let (data, response) = try await OperatorSession.data(for: request)
      guard (response as? HTTPURLResponse)?.statusCode == 200 else {
        message = httpDetail(data) ?? "Replay failed"
        return
      }
      replay = try JSONCoding.decoder().decode(ModelReplayRun.self, from: data)
      message = "Fable, Astra, and Jev finished. Results are on this screen and under Report."
    } catch { message = error.localizedDescription }
  }

  private func httpDetail(_ data: Data) -> String? {
    guard let payload = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
      return nil
    }
    return payload["detail"] as? String
  }
}

struct ModelReplayResultBlock: View {
  let run: ModelReplayRun
  var title: String?

  var body: some View {
    VStack(alignment: .leading, spacing: 8) {
      if let title {
        Text("Assessed as \(title)").font(.headline)
      }
      if let decision = run.decision {
        Text("Policy: \(decision.action.replacingOccurrences(of: "_", with: " ")) · \(decision.reason.replacingOccurrences(of: "_", with: " "))")
      }
      if let comparison = run.comparison {
        Text(
          "Jev comparison · disagreement: \(comparison.disagreement == true ? "yes" : "no") · route: \((comparison.chosenRoute ?? "none").replacingOccurrences(of: "_", with: " ")) \(confidence(comparison.confidence))"
        )
        .font(.footnote)
        if comparison.writesCount == true || comparison.writesPrice == true {
          AccessibleStatusLabel(text: "Unexpected model write", kind: .error)
        }
      }
      if run.partial == true {
        AccessibleStatusLabel(
          text: "Disclosed partial. Human review. No invented assessment.",
          kind: .warning
        )
      }
      assessment("Fable", run.assessments?.fable)
      assessment("Astra", run.assessments?.astraReplay)
      if let jev = run.jev {
        Text("Jev: \((jev.choice ?? "no choice").replacingOccurrences(of: "_", with: " ")) \(confidence(jev.confidence))")
      }
      if let failures = run.failures, !failures.isEmpty {
        AccessibleStatusLabel(
          text: "Failures: \(failures.map { "\($0.key) \($0.value)" }.joined(separator: ", "))",
          kind: .error
        )
      }
    }
  }

  @ViewBuilder
  private func assessment(_ name: String, _ row: ModelReplayAssessment?) -> some View {
    if let row {
      VStack(alignment: .leading, spacing: 2) {
        Text("\(name): \((row.condition ?? "unknown")) · \((row.category ?? "unknown")) \(confidence(row.confidence))")
        if let action = row.recommendedAction {
          Text(action.replacingOccurrences(of: "_", with: " ")).font(.caption)
        }
        if let identity = row.identityCandidates?.first?.label {
          Text("Named: \(identity)").font(.caption)
        }
        if let rationale = row.rationale, !rationale.isEmpty {
          Text(rationale).font(.footnote)
        }
      }
    } else {
      AccessibleStatusLabel(text: "\(name): not returned", kind: .warning)
    }
  }

  private func confidence(_ value: Double?) -> String {
    guard let value else { return "" }
    return String(format: "· %.0f%%", value * 100)
  }
}
