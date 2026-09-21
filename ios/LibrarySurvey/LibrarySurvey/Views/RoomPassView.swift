import SwiftUI

struct RoomPassView: View {
  @ObservedObject var store: RoomCaptureStore
  @ObservedObject var camera: CameraSessionCoordinator
  @ObservedObject var audio: AudioNoteRecorder
  @ObservedObject var shelves: ShelfCaptureStore
  let recordSpokenNotes: Bool
  let sealError: String?
  let onContinue: () -> Void

  var body: some View {
    ZStack(alignment: .bottom) {
      RoomCaptureContainer(store: store, camera: camera)
        .ignoresSafeArea(edges: .bottom)

      controls
        .padding()
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 20))
        .padding()
    }
  }

  private var isCapturing: Bool {
    if case .capturing = store.state { return true }
    return false
  }

  private var controls: some View {
    VStack(spacing: 10) {
      Text(camera.statusLine)
        .font(.caption)
        .foregroundStyle(.secondary)
      if let message = audio.errorMessage {
        Text(message).font(.caption).foregroundStyle(.orange)
      }
      if let message = sealError {
        Text(message).font(.caption).foregroundStyle(.red)
      }
      switch store.state {
      case .ready, .failed:
        if case let .failed(detail) = store.state {
          Text(detail).font(.caption).foregroundStyle(.red)
        }
        Button("Start Scan", systemImage: "record.circle", action: start)
          .buttonStyle(.borderedProminent)
        Text("Close-ups are a later still after this geometry session is released. Optical zoom is not available during RoomPlan.")
          .font(.caption2)
          .foregroundStyle(.secondary)
      case .capturing:
        capturingBar
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
        sequentialStatus
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

  private var capturingBar: some View {
    HStack {
      Button("Pause Scan", systemImage: "pause.fill", action: pause)
        .buttonStyle(.bordered)
      Button("Finish Room Scan", systemImage: "stop.fill", action: stop)
        .buttonStyle(.borderedProminent)
    }
  }

  @ViewBuilder
  private var sequentialStatus: some View {
    if let detail = store.sequentialFallbackDetail {
      Label(detail, systemImage: "camera.badge.ellipsis")
        .font(.caption)
        .foregroundStyle(.orange)
        .accessibilityLabel("Sequential fallback")
    } else {
      Text("RGB frames were copied from RoomPlan's AR session. Optical zoom is not available on this pass. Close-ups are a later still after this session is released.")
        .font(.caption2)
        .foregroundStyle(.secondary)
    }
  }

  private var status: String {
    switch store.state {
    case .capturing: "Scanning room geometry"
    case .paused: "Paused"
    case .processing: "Processing scan"
    case .captured: "Room captured"
    default: ""
    }
  }

  private func start() {
    store.start()
    guard isCapturing else { return }
    shelves.clearLiveAssist()
    if !shelves.units.isEmpty {
      shelves.units[0].roomName = store.roomName
    }
    guard recordSpokenNotes else { return }
    let directory = FileManager.default.temporaryDirectory
      .appendingPathComponent("LibrarySurveyAudio", isDirectory: true)
    audio.start(in: directory)
  }

  private func pause() {
    store.pause()
  }

  private func resume() {
    store.resume()
  }

  private func stop() {
    store.stop()
  }

  private func reset() {
    audio.stop()
    shelves.clearLiveAssist()
    store.reset()
  }
}
