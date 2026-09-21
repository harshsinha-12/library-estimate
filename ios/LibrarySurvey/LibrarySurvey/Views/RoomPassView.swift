import SwiftUI

struct RoomPassView: View {
  @ObservedObject var store: RoomCaptureStore
  @ObservedObject var audio: AudioNoteRecorder
  @ObservedObject var exceptions: ExceptionCaptureStore
  @Binding var notes: [WrittenNote]
  let recordSpokenNotes: Bool
  let sealError: String?
  let onContinue: () -> Void

  @State private var noteText = ""
  @State private var showObjectMark = false
  @State private var pointedCategory: AssetCategory = .portrait
  @State private var pointedLabel = ""
  @State private var pointedHighValue = false
  @State private var pointedCost = ""
  @State private var pointedCurrency = "INR"

  var body: some View {
    ZStack(alignment: .bottom) {
      RoomCaptureContainer(store: store)
        .ignoresSafeArea(edges: .bottom)

      if isCapturing {
        capturingBar
      } else {
        ScrollView {
          VStack(spacing: 12) {
            status
            TextField("Room name", text: $store.roomName)
              .textFieldStyle(.roundedBorder)

            HStack {
              TextField("Written note", text: $noteText)
                .textFieldStyle(.roundedBorder)
              Button("Add", systemImage: "plus", action: addNote)
                .disabled(noteText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }

            if audio.isRecording {
              Label("Recording spoken notes", systemImage: "waveform.circle.fill")
                .foregroundStyle(.red)
            }
            if !notes.isEmpty {
              Text("\(notes.count) written note\(notes.count == 1 ? "" : "s") on the capture clock")
                .font(.caption)
            }
            if let message = audio.errorMessage {
              Text(message).font(.caption).foregroundStyle(.orange)
            }
            if let message = sealError {
              Text(message).font(.caption).foregroundStyle(.red)
            }
            actions
          }
          .padding()
        }
        .frame(maxHeight: 330)
        .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 24))
        .padding()
      }
    }
    .sheet(isPresented: $showObjectMark) {
      NavigationStack {
        Form {
          if let image = exceptions.latestImage {
            Image(uiImage: image).resizable().scaledToFit().frame(maxHeight: 220)
          }
          Picker("Object", selection: $pointedCategory) {
            ForEach(AssetCategory.allCases) { item in Text(item.label).tag(item) }
          }
          TextField("Object label", text: $pointedLabel)
          Toggle("High-value or unusual", isOn: $pointedHighValue)
          TextField("Stated replacement cost", text: $pointedCost)
            .keyboardType(.decimalPad)
          Picker("Currency", selection: $pointedCurrency) {
            Text("INR").tag("INR")
            Text("EUR").tag("EUR")
            Text("JPY").tag("JPY")
            Text("USD").tag("USD")
          }
          Button("Mark pointed object") {
            exceptions.addMark(
              category: pointedCategory,
              label: pointedLabel,
              room: store.roomName,
              highValue: pointedHighValue,
              statedCost: Double(pointedCost),
              statedCurrency: pointedCost.isEmpty ? nil : pointedCurrency
            )
            pointedLabel = ""
            pointedCost = ""
            showObjectMark = false
          }
          .disabled(pointedLabel.isEmpty)
        }
        .navigationTitle("Pointed object")
      }
    }
  }

  private var isCapturing: Bool {
    if case .capturing = store.state { return true }
    return false
  }

  private var capturingBar: some View {
    Button("Finish Room Scan", systemImage: "stop.fill", action: stop)
      .buttonStyle(.borderedProminent)
      .padding(.horizontal, 16)
      .padding(.vertical, 10)
      .background(.ultraThinMaterial, in: Capsule())
      .padding()
  }

  @ViewBuilder
  private var status: some View {
    switch store.state {
    case .ready:
      Label("Ready for RoomPlan Pass A", systemImage: "viewfinder")
    case .capturing:
      Label("Scanning room and sampling RGB + poses", systemImage: "record.circle")
    case .processing:
      ProgressView("RoomPlan is processing")
    case .captured:
      Label(
        "Captured \(store.rooms.count) room; \(store.samples.count) synchronized frames",
        systemImage: "checkmark.circle.fill"
      )
      .foregroundStyle(.green)
    case .unsupported:
      Label("RoomPlan is unavailable on this device", systemImage: "xmark.octagon")
        .foregroundStyle(.red)
    case let .failed(message):
      Text(message).foregroundStyle(.red)
    }
  }

  @ViewBuilder
  private var actions: some View {
    switch store.state {
    case .ready, .failed:
      Button("Start Room Scan", systemImage: "camera.viewfinder", action: start)
        .buttonStyle(.borderedProminent)
    case .capturing:
      HStack {
        Button("Point out object", systemImage: "hand.point.up.left", action: pointOutObject)
          .buttonStyle(.bordered)
        Button("Finish Room Scan", systemImage: "stop.fill", action: stop)
          .buttonStyle(.borderedProminent)
      }
    case .processing:
      EmptyView()
    case .captured:
      HStack {
        Button("Scan Again", systemImage: "arrow.counterclockwise", action: store.reset)
          .buttonStyle(.bordered)
        Button("Continue to Shelf Map", systemImage: "books.vertical", action: onContinue)
          .buttonStyle(.borderedProminent)
      }
    case .unsupported:
      EmptyView()
    }
  }

  private func start() {
    store.start()
    guard recordSpokenNotes else { return }
    let directory = FileManager.default.temporaryDirectory
      .appendingPathComponent("LibrarySurveyAudio", isDirectory: true)
    audio.start(in: directory)
  }

  private func stop() {
    store.stop()
  }

  private func pointOutObject() {
    guard let sample = store.currentFrameSample(),
          let image = UIImage(data: sample.jpegData) else { return }
    exceptions.currentCameraPose = sample.cameraTransform
    exceptions.capture(image)
    exceptions.pointAtOther()
    showObjectMark = true
  }

  private func addNote() {
    let trimmed = noteText.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else { return }
    notes.append(
      WrittenNote(
        id: UUID(),
        text: trimmed,
        createdAt: Date(),
        monotonicSeconds: MonotonicClock.now
      )
    )
    noteText = ""
  }
}
