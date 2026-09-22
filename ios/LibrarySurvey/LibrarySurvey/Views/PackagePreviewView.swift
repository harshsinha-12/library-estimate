import SwiftUI

struct PackagePreviewView: View {
  let package: SealedSurveyPackage
  let draft: SurveyDraft
  @ObservedObject var uploader: SurveyUploadService
  let onNewSurvey: () -> Void
  @AppStorage("backendURL") private var backendURLString = "http://192.168.29.178:8000"
  @State private var layout: FloorPlanLayout?
  @State private var sharePlanURL: URL?
  @State private var operatorToken = ""

  var body: some View {
    ScrollView {
      VStack(alignment: .leading, spacing: 20) {
        AccessibleStatusLabel(text: "Hashes verified locally", kind: .success)

        PackageMediaPreviews(
          layout: layout,
          usdzURL: package.usdzURL,
          svgURL: package.svgURL
        )

        GroupBox("Package") {
          VStack(alignment: .leading, spacing: 8) {
            Text("Survey: \(draft.displayName)")
            Text("Geography: \(draft.geography.city), \(draft.geography.countryCode)")
            Text("Source: \(draft.geography.source.rawValue)")
            Text("Files: \(package.manifest.files.count)")
            if let shelf = layout?.shelves.first {
              Text("Shelf data size: \(shelf.copyCountLabel) · \(shelf.fillLabel)")
            }
            ShareLink(item: sharePlanURL ?? package.svgURL) {
              Label("Share floor plan", systemImage: "square.and.arrow.up")
            }
          }
          .frame(maxWidth: .infinity, alignment: .leading)
        }

        GroupBox("Backend") {
          VStack(alignment: .leading, spacing: 8) {
            TextField("http://192.168.29.178:8000", text: $backendURLString)
              .textInputAutocapitalization(.never)
              .keyboardType(.URL)
              .autocorrectionDisabled()
            Text("Mac and iPhone must share Wi-Fi. Uvicorn must bind 0.0.0.0, not 127.0.0.1.")
              .font(.caption)
              .foregroundStyle(.secondary)
            SecureField("Operator token", text: $operatorToken)
              .textInputAutocapitalization(.never)
              .autocorrectionDisabled()
              .onChange(of: operatorToken) { _, value in
                OperatorCredentials.save(value)
              }
            Text("Stored in this device's Keychain. A token requires an HTTPS backend URL.")
              .font(.caption)
              .foregroundStyle(.secondary)
            Button("Test Connection", systemImage: "wifi") {
              Task { await pingBackend() }
            }
            .disabled(uploader.isBusy || backendURL == nil)
            .minimumScaledTouchTarget()
          }
          .frame(maxWidth: .infinity, alignment: .leading)
        }

        VStack(alignment: .leading, spacing: 8) {
          if uploader.progress > 0 && uploader.progress < 1 {
            ProgressView(value: uploader.progress) { Text(uploader.status) }
          } else {
            Text(uploader.status).font(.callout)
          }
          if let error = uploader.errorMessage {
            AccessibleStatusLabel(text: error, kind: .error)
            Text("The local package is intact. Retry resumes after the last acknowledged file.")
              .font(.caption)
              .foregroundStyle(.secondary)
          }
        }

        Button("Upload or Resume", systemImage: "arrow.up.circle.fill") {
          Task { await startUpload() }
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .disabled(uploader.isBusy || backendURL == nil)
        .minimumScaledTouchTarget()

        GroupBox("Results") {
          VStack(alignment: .leading, spacing: 10) {
            if let backendURL {
              if let layout {
                NavigationLink {
                  SurveySpatialEvidenceView(
                    surveyId: package.surveyId, backendURL: backendURL,
                    layout: layout, usdzURL: package.usdzURL
                  )
                } label: {
                  Label("2D and 3D shelf evidence", systemImage: "square.3.layers.3d")
                }
              }
              NavigationLink {
                ProcessingView(surveyId: package.surveyId, backendURL: backendURL)
              } label: {
                Label("Processing status", systemImage: "clock.arrow.circlepath")
              }
              NavigationLink {
                OverviewView(surveyId: package.surveyId, backendURL: backendURL)
              } label: {
                Label("Overview, building, and spend", systemImage: "chart.bar.doc.horizontal")
              }
              NavigationLink {
                InventoryView(surveyId: package.surveyId, backendURL: backendURL)
              } label: {
                Label("Inventory, prices, Fable / Astra / Jev", systemImage: "books.vertical")
              }
              NavigationLink {
                Stage3ReviewView(surveyId: package.surveyId, backendURL: backendURL)
              } label: {
                Label("Review unresolved objects and notes", systemImage: "checklist")
              }
              NavigationLink {
                ReportView(surveyId: package.surveyId, backendURL: backendURL)
              } label: {
                Label("Report, PDF, and model runs", systemImage: "doc.text")
              }
            } else {
              Text("Set the backend URL above to open overview, inventory, and review.")
                .font(.footnote)
                .foregroundStyle(.secondary)
            }
          }
          .frame(maxWidth: .infinity, alignment: .leading)
        }

        Button("Start Another Survey", action: onNewSurvey)
          .buttonStyle(.bordered)
          .minimumScaledTouchTarget()
      }
      .padding()
    }
    .onAppear(perform: loadPlan)
    .onAppear { operatorToken = OperatorCredentials.load() }
    .accessibilityStatusAnnouncements(uploaderAccessibilityStatus)
  }

  private var uploaderAccessibilityStatus: String {
    if let error = uploader.errorMessage { return "Upload error. \(error)" }
    return uploader.status
  }

  private var backendURL: URL? {
    URL(string: backendURLString.trimmingCharacters(in: .whitespacesAndNewlines))
  }

  private func pingBackend() async {
    guard let backendURL else {
      uploader.report("Enter a valid backend URL, for example http://192.168.29.178:8000")
      return
    }
    await uploader.ping(backendURL: backendURL)
  }

  private func startUpload() async {
    guard let backendURL else { return }
    await uploader.upload(package: package, draft: draft, backendURL: backendURL)
  }

  private func loadPlan() {
    let structureURL = package.rootURL.appendingPathComponent("roomplan/processed/structure.json")
    if layout == nil {
      layout = try? FloorPlanLayout.load(from: structureURL)
    }
    let labeledURL = package.rootURL.appendingPathComponent("shelf_scans/labeled.json")
    if let data = try? Data(contentsOf: labeledURL),
       let labeled = try? JSONCoding.decoder().decode(LabeledShelfPackage.self, from: data) {
      var unique: [String: LabeledPass] = [:]
      for item in labeled.passes { unique[item.faceId] = item }
      layout?.shelves = unique.values.map { pass in
        let placement = pass.placement ?? ShelfFootprint.unregisteredKind
        return FloorPlanLayout.ShelfOverlay(
          faceId: pass.faceId,
          label: pass.label,
          minX: pass.minX,
          minZ: pass.minZ,
          maxX: pass.maxX,
          maxZ: pass.maxZ,
          copyCountLabel: "\(pass.rows.reduce(0) { $0 + $1.spines.count }) copies",
          fillLabel: placement == ShelfFootprint.operatorKind ? "operator-placed" : "unregistered overlay",
          status: pass.rows.contains(where: { $0.coverage < 0.8 }) ? "partial" : "ok",
          placement: placement
        )
      }
    }
    if sharePlanURL == nil, let layout {
      let url = FileManager.default.temporaryDirectory
        .appendingPathComponent("tagged-plan-\(package.surveyId.uuidString).svg")
      try? Data(layout.svg().utf8).write(to: url, options: .atomic)
      sharePlanURL = url
    }
  }
}

private struct PackageMediaPreviews: View {
  let layout: FloorPlanLayout?
  let usdzURL: URL
  let svgURL: URL

  var body: some View {
    GroupBox("2D tagged floor plan") {
      if let layout {
        TaggedFloorPlanView(layout: layout)
      } else {
        FloorPlanPreview(url: svgURL)
          .frame(height: 280)
          .accessibilityLabel("Estimated RoomPlan wall outline preview")
          .accessibilityHint("Static visual preview. Open spatial evidence for the tagged shelf list.")
      }
    }
    GroupBox("3D RoomPlan model") {
      RoomModelPreview(url: usdzURL)
        .frame(height: 280)
        .clipShape(RoundedRectangle(cornerRadius: 16, style: .continuous))
    }
  }
}

private struct FloorPlanPreview: View {
  let url: URL
  @State private var plan: FloorPlanPaths?

  var body: some View {
    Canvas { context, size in
      let background = Path(CGRect(origin: .zero, size: size))
      context.fill(background, with: .color(Color(red: 0.11, green: 0.11, blue: 0.12)))
      guard let plan, plan.width > 0, plan.height > 0 else { return }
      let scale = min(size.width / plan.width, size.height / plan.height)
      for line in plan.lines {
        var path = Path()
        path.move(to: CGPoint(x: line.x1 * scale, y: line.y1 * scale))
        path.addLine(to: CGPoint(x: line.x2 * scale, y: line.y2 * scale))
        let color = line.colorHex.map { Color(floorPlanHex: $0) }
          ?? Color(red: 0.23, green: 0.48, blue: 1)
        context.stroke(path, with: .color(color), style: StrokeStyle(lineWidth: 4, lineCap: .square))
      }
    }
    .onAppear {
      if plan == nil {
        plan = FloorPlanPaths.load(from: url)
      }
    }
  }
}

private struct FloorPlanPaths {
  struct Line {
    let x1: CGFloat
    let y1: CGFloat
    let x2: CGFloat
    let y2: CGFloat
    let colorHex: String?
  }

  var width: CGFloat
  var height: CGFloat
  var lines: [Line]

  static func load(from url: URL) -> FloorPlanPaths? {
    guard let svg = try? String(contentsOf: url, encoding: .utf8) else { return nil }
    var width: CGFloat = 400
    var height: CGFloat = 280
    if let box = svg.range(of: #"viewBox="0 0 ([0-9.]+) ([0-9.]+)""#, options: .regularExpression) {
      let values = svg[box]
        .split(whereSeparator: { $0 == "\"" || $0 == " " })
        .compactMap { Double(String($0)) }
      if values.count >= 2 {
        width = CGFloat(values[values.count - 2])
        height = CGFloat(values[values.count - 1])
      }
    }
    let pattern = #"x1="([0-9.]+)" y1="([0-9.]+)" x2="([0-9.]+)" y2="([0-9.]+)"(?: stroke="(#[0-9A-Fa-f]+)")?"#
    guard let regex = try? NSRegularExpression(pattern: pattern) else { return nil }
    let range = NSRange(svg.startIndex..<svg.endIndex, in: svg)
    let lines = regex.matches(in: svg, range: range).compactMap { match -> Line? in
      func value(_ index: Int) -> CGFloat? {
        guard let swiftRange = Range(match.range(at: index), in: svg),
              let number = Double(String(svg[swiftRange]))
        else { return nil }
        return CGFloat(number)
      }
      guard let x1 = value(1), let y1 = value(2), let x2 = value(3), let y2 = value(4) else {
        return nil
      }
      var colorHex: String?
      if match.range(at: 5).location != NSNotFound,
         let colorRange = Range(match.range(at: 5), in: svg) {
        colorHex = String(svg[colorRange])
      }
      return Line(x1: x1, y1: y1, x2: x2, y2: y2, colorHex: colorHex)
    }
    return FloorPlanPaths(width: width, height: height, lines: lines)
  }
}
