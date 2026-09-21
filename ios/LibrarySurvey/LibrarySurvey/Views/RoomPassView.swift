import SwiftUI

struct RoomPassView: View {
  @ObservedObject var store: RoomCaptureStore
  @ObservedObject var audio: AudioNoteRecorder
  @Binding var notes: [WrittenNote]
  let recordSpokenNotes: Bool
  let sealError: String?
  let onSeal: () -> Void

  @State private var noteText = ""

  var body: some View {
    ZStack(alignment: .bottom) {
      RoomCaptureContainer(store: store)
        .ignoresSafeArea(edges: .bottom)

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
      Button("Finish Room Scan", systemImage: "stop.fill", action: stop)
        .buttonStyle(.borderedProminent)
    case .processing:
      EmptyView()
    case .captured:
      HStack {
        Button("Scan Again", systemImage: "arrow.counterclockwise", action: store.reset)
          .buttonStyle(.bordered)
        Button("Seal Package", systemImage: "lock.fill", action: onSeal)
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
    audio.stop()
    store.stop()
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

