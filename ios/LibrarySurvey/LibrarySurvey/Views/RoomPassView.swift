import Combine
import SwiftUI

struct RoomPassView: View {
  @ObservedObject var store: RoomCaptureStore
  @ObservedObject var audio: AudioNoteRecorder
  @ObservedObject var shelves: ShelfCaptureStore
  let recordSpokenNotes: Bool
  let sealError: String?
  let onContinue: () -> Void

  var body: some View {
    ZStack(alignment: .bottom) {
      RoomCaptureContainer(store: store)
        .ignoresSafeArea(edges: .bottom)

      if isCapturing {
        GeometryReader { geometry in
          ForEach(Array(shelves.highlightedSpines.enumerated()), id: \.offset) { index, box in
            let rect = shelves.displayRect(for: box, in: geometry.size)
            RoundedRectangle(cornerRadius: 4)
              .stroke(.yellow, lineWidth: 2)
              .background(.yellow.opacity(0.12))
              .overlay(alignment: .top) {
                Text("Possible book \(index + 1)")
                  .font(.caption2.bold())
                  .padding(3)
                  .background(.yellow, in: RoundedRectangle(cornerRadius: 3))
                  .foregroundStyle(.black)
              }
              .frame(width: max(8, rect.width), height: max(16, rect.height))
              .position(x: rect.midX, y: rect.midY)
              .accessibilityLabel("Possible book \(index + 1)")
          }
        }
        .allowsHitTesting(false)
      }

      controls
        .padding()
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 20))
        .padding()
    }
    .onReceive(Timer.publish(every: 0.4, on: .main, in: .common).autoconnect()) { _ in
      if isCapturing { shelves.ingestCurrentFrame() }
    }
  }

  private var isCapturing: Bool {
    if case .capturing = store.state { return true }
    return false
  }

  private var controls: some View {
    VStack(spacing: 10) {
      if isCapturing, shelves.assistCount > 0 {
        Text("\(shelves.assistCount) possible books in view · verify after scan")
          .font(.caption)
      }
      if let message = audio.errorMessage {
        Text(message).font(.caption).foregroundStyle(.orange)
      }
      if let message = sealError {
        Text(message).font(.caption).foregroundStyle(.red)
      }
      switch store.state {
      case .ready, .failed:
        Button("Start Scan", systemImage: "record.circle", action: start)
          .buttonStyle(.borderedProminent)
      case .capturing:
        HStack {
          Button("Pause Scan", systemImage: "pause.fill", action: pause)
            .buttonStyle(.bordered)
          Button("Stop Scan", systemImage: "stop.fill", action: stop)
            .buttonStyle(.borderedProminent)
        }
      case .paused:
        HStack {
          Button("Resume Scan", systemImage: "play.fill", action: resume)
            .buttonStyle(.borderedProminent)
          Button("Stop Scan", systemImage: "stop.fill", action: stop)
            .buttonStyle(.bordered)
        }
      case .processing:
        ProgressView("Processing scan")
      case .captured:
        HStack {
          Button("Scan Again", systemImage: "arrow.counterclockwise", action: reset)
            .buttonStyle(.bordered)
          Button("Review Capture", systemImage: "books.vertical", action: onContinue)
            .buttonStyle(.borderedProminent)
        }
      case .unsupported:
        Text("RoomPlan is unavailable on this device.")
          .foregroundStyle(.red)
      }
    }
  }

  private func start() {
    store.start()
    guard isCapturing else { return }
    if let session = store.arSession, !shelves.units.isEmpty {
      shelves.units[0].roomName = store.roomName
      shelves.attach(session: session)
      shelves.startFace(unit: shelves.units[0], face: .a)
    }
    guard recordSpokenNotes else { return }
    let directory = FileManager.default.temporaryDirectory
      .appendingPathComponent("LibrarySurveyAudio", isDirectory: true)
    audio.start(in: directory)
  }

  private func pause() {
    shelves.pauseFace()
    store.pause()
  }

  private func resume() {
    store.resume()
    shelves.resumeFace()
  }

  private func stop() {
    shelves.stopFace()
    store.stop()
  }

  private func reset() {
    audio.stop()
    shelves.captures = []
    store.reset()
  }
}
