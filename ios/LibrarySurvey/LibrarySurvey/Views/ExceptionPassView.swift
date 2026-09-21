import AVFoundation
import SwiftUI
import UIKit

struct ExceptionPassView: View {
  @ObservedObject var store: ExceptionCaptureStore
  let units: [ShelfUnit]
  let shelfPackage: LabeledShelfPackage
  let onDone: () -> Void

  @State private var showCamera = false
  @State private var category: AssetCategory = .portrait
  @State private var label = ""
  @State private var room = "Library"
  @State private var highValue = false
  @State private var selectedAssetId = ""
  @State private var faceId = ""
  @State private var row = 1
  @State private var slot = 0
  @State private var kind = "barcode"
  @State private var identifierKind = "auto"
  @State private var title = ""
  @State private var scope = "volume"
  @State private var damageType = "tear"
  @State private var severity = "unknown"
  @State private var region = ""
  @State private var closeupRef: String?
  @State private var scaleRef: String?
  @State private var noteText = ""
  @State private var promptPlayer: AVAudioPlayer?
  @AppStorage("backendURL") private var backendURLString = "http://192.168.29.178:8000"

  var body: some View {
    Form {
      Section("Exception queue") {
        Text("Capture unread spines, barcodes, title pages, damage, high-value objects, and unbound notes. Each item stays visible for review.")
          .font(.footnote)
        Text("Captured: \(store.scans.count) scans · \(store.marks.count) other assets · \(store.notes.count) notes")
        ForEach(pendingSpines, id: \.key) { item in
          Button("Unread spine: \(item.face) / \(item.row) / slot \(item.slot)") {
            faceId = item.face
            row = Int(item.row.replacingOccurrences(of: "row_", with: "")) ?? 1
            slot = item.slot
            kind = "barcode"
          }
        }
        ForEach(store.marks.filter(\.highValue)) { mark in
          Text("High-value review: \(mark.label)").foregroundStyle(.orange)
        }
        ForEach(store.notes.filter { $0.tappedAssetId == nil && $0.faceId == nil }) { note in
          Text("Unbound note: \(note.text)").foregroundStyle(.orange)
        }
        ForEach(store.scans.filter { $0.kind == "damage" && ($0.closeupRef == nil || $0.scaleRef == nil) }) { scan in
          Text("Damage needs close-up and scale: \(scan.id)").foregroundStyle(.orange)
        }
      }
      Section("Camera and Vision") {
        Button("Capture image", systemImage: "camera") { showCamera = true }
        if let image = store.latestImage {
          Image(uiImage: image)
            .resizable()
            .scaledToFit()
            .frame(maxHeight: 280)
            .overlay {
              GeometryReader { geometry in
                ForEach(Array(store.candidateRegions.enumerated()), id: \.offset) { index, box in
                  Button {
                    store.focus(on: box)
                  } label: {
                    RoundedRectangle(cornerRadius: 5)
                      .stroke(.yellow, lineWidth: 3)
                      .background(.yellow.opacity(0.08))
                  }
                  .frame(width: max(44, box.width * geometry.size.width),
                         height: max(44, box.height * geometry.size.height))
                  .position(x: box.midX * geometry.size.width,
                            y: (1 - box.midY) * geometry.size.height)
                  .accessibilityLabel("Candidate object \(index + 1). Tap to zoom and inspect")
                }
              }
            }
          Text("Tap an outlined object to zoom into its image while audio continues recording.")
            .font(.footnote)
        }
        if let suggested = store.suggestedCategory {
          Text("Vision suggests \(suggested.label); confirm the category below.")
            .font(.caption)
        }
        if let path = store.latestImageRef { Text(path).font(.caption2) }
        if !store.latestBarcode.isEmpty {
          TextField("Observed barcode", text: $store.latestBarcode)
            .textInputAutocapitalization(.characters)
        } else {
          TextField("Type visible barcode", text: $store.latestBarcode)
            .textInputAutocapitalization(.characters)
        }
        if !store.latestText.isEmpty { Text(store.latestText).font(.caption) }
        if let error = store.errorMessage { Text(error).foregroundStyle(.orange) }
        HStack {
          Button("Speak barcode prompt") { Task { await playPrompt("barcode") } }
          Button("Speak damage prompt") { Task { await playPrompt("damage") } }
        }
      }
      Section("Mark another asset") {
        Picker("Category", selection: $category) {
          ForEach(AssetCategory.allCases) { item in Text(item.label).tag(item) }
        }
        TextField("Object label", text: $label)
        TextField("Room", text: $room)
        Toggle("High-value or unusual", isOn: $highValue)
        Button("Count asset") {
          store.addMark(category: category, label: label, room: room, highValue: highValue)
          selectedAssetId = store.marks.last?.assetCopyId ?? ""
          label = ""
        }
        .disabled(label.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
        Text("Mugs are counted and excluded from valuation. Art requires appraisal.")
          .font(.footnote)
      }
      Section("Bind the scan to a physical object") {
        Picker("Marked object", selection: $selectedAssetId) {
          Text("Use shelf slot").tag("")
          ForEach(store.marks) { mark in Text(mark.label).tag(mark.assetCopyId) }
        }
        Picker("Shelf face", selection: $faceId) {
          Text("Select face").tag("")
          ForEach(units) { unit in
            Text("\(unit.name) A").tag(unit.faceAId)
            Text("\(unit.name) B").tag(unit.faceBId)
          }
        }
        Stepper("Row \(row)", value: $row, in: 1...12)
        Stepper("Slot \(slot)", value: $slot, in: 0...100)
      }
      Section("Pass C scan") {
        Picker("Type", selection: $kind) {
          Text("Barcode").tag("barcode")
          Text("Title page").tag("title_page")
          Text("Damage").tag("damage")
        }
        if kind != "damage" {
          Picker("Identifier type", selection: $identifierKind) {
            Text("Detect").tag("auto")
            Text("ISBN").tag("isbn")
            Text("ISSN").tag("issn")
            Text("Library barcode").tag("library_barcode")
          }
          TextField("Title from page", text: $title)
          Picker("Set or volume", selection: $scope) {
            Text("Volume").tag("volume")
            Text("Boxed set").tag("set")
          }
        } else {
          TextField("Damage type", text: $damageType)
          TextField("Severity candidate", text: $severity)
          TextField("Region, e.g. lower right", text: $region)
          Button("Use current image as close-up") { closeupRef = store.latestImageRef }
          Button("Use current image as scale reference") { scaleRef = store.latestImageRef }
          Text("Close-up: \(closeupRef == nil ? "needed" : "captured") · Scale: \(scaleRef == nil ? "needed" : "captured")")
            .font(.caption)
        }
        Button("Add exception scan") {
          store.addScan(kind: kind, assetId: selectedAssetId.isEmpty ? nil : selectedAssetId,
                        faceId: faceId.isEmpty ? nil : faceId,
                        rowId: String(format: "row_%02d", row), slot: slot,
                        identifierKind: identifierKind, title: title, scope: scope,
                        damageType: damageType, severity: severity, region: region,
                        closeupRef: closeupRef, scaleRef: scaleRef)
        }
        .disabled(selectedAssetId.isEmpty && faceId.isEmpty)
      }
      Section("Speak / write note") {
        TextField("What did you observe?", text: $noteText, axis: .vertical)
        Button("Add note on capture clock") {
          store.addNote(text: noteText, assetId: selectedAssetId.isEmpty ? nil : selectedAssetId,
                        category: selectedAssetId.isEmpty ? nil : category,
                        faceId: faceId.isEmpty ? nil : faceId,
                        rowId: faceId.isEmpty ? nil : String(format: "row_%02d", row),
                        slot: faceId.isEmpty ? nil : slot)
          noteText = ""
        }
        .disabled(noteText.isEmpty)
        Text("A note is an assertion. An ambiguous note stays unbound for review.")
          .font(.footnote)
      }
      Section {
        Button("Return to Shelf Map", action: onDone)
          .buttonStyle(.borderedProminent)
      }
    }
    .sheet(isPresented: $showCamera) {
      ExceptionCamera { image in store.capture(image) }
    }
    .onAppear {
      if let focusedFaceId = store.focusedFaceId {
        faceId = focusedFaceId
        row = store.focusedRow
        slot = store.focusedSlot
      }
    }
    .onChange(of: store.suggestedCategory) { _, suggestion in
      if let suggestion { category = suggestion }
    }
  }

  private struct PendingSpine {
    let face: String
    let row: String
    let slot: Int
    var key: String { "\(face)/\(row)/\(slot)" }
  }

  private var pendingSpines: [PendingSpine] {
    var found: [String: PendingSpine] = [:]
    for pass in shelfPackage.passes {
      for row in pass.rows {
        for spine in row.spines {
          let item = PendingSpine(face: pass.faceId, row: row.rowId, slot: spine.slot)
          found[item.key] = item
        }
      }
    }
    let scanned = Set(store.scans.compactMap { scan -> String? in
      guard let face = scan.faceId, let row = scan.rowId, let slot = scan.slot else { return nil }
      return "\(face)/\(row)/\(slot)"
    })
    return found.values.filter { !scanned.contains($0.key) }.sorted { $0.key < $1.key }
  }

  @MainActor
  private func playPrompt(_ id: String) async {
    guard let base = URL(string: backendURLString) else { return }
    do {
      var request = URLRequest(url: base.appendingPathComponent("v1/operator-prompts/\(id)/speech"))
      request.httpMethod = "POST"
      let (audio, response) = try await URLSession.shared.data(for: request)
      guard (response as? HTTPURLResponse)?.statusCode == 200 else {
        throw URLError(.badServerResponse)
      }
      promptPlayer = try AVAudioPlayer(data: audio)
      promptPlayer?.play()
    } catch {
      store.errorMessage = "Prompt audio unavailable. Follow the written instruction."
    }
  }
}

struct ExceptionCamera: UIViewControllerRepresentable {
  let onCapture: (UIImage) -> Void
  @Environment(\.dismiss) private var dismiss

  func makeUIViewController(context: Context) -> UIImagePickerController {
    let picker = UIImagePickerController()
    picker.sourceType = UIImagePickerController.isSourceTypeAvailable(.camera) ? .camera : .photoLibrary
    picker.delegate = context.coordinator
    return picker
  }

  func updateUIViewController(_ controller: UIImagePickerController, context: Context) {}

  func makeCoordinator() -> Coordinator { Coordinator(onCapture: onCapture, dismiss: dismiss) }

  final class Coordinator: NSObject, UINavigationControllerDelegate, UIImagePickerControllerDelegate {
    let onCapture: (UIImage) -> Void
    let dismiss: DismissAction
    init(onCapture: @escaping (UIImage) -> Void, dismiss: DismissAction) {
      self.onCapture = onCapture
      self.dismiss = dismiss
    }
    func imagePickerController(_ picker: UIImagePickerController,
                               didFinishPickingMediaWithInfo info: [UIImagePickerController.InfoKey: Any]) {
      if let image = info[.originalImage] as? UIImage { onCapture(image) }
      dismiss()
    }
    func imagePickerControllerDidCancel(_ picker: UIImagePickerController) { dismiss() }
  }
}
