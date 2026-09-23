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
        .accessibilityLabel("Live RoomPlan geometry capture")
        .accessibilityHint("Move through the room while following the visible controls. This view captures geometry and is not an optical zoom camera.")

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
        .accessibilityLabel("Camera status")
        .accessibilityValue(camera.statusLine)
      if let message = audio.errorMessage {
        AccessibleStatusLabel(text: message, kind: .warning).font(.caption)
      }
      if let message = sealError {
        AccessibleStatusLabel(text: message, kind: .error).font(.caption)
      }
      switch store.state {
      case .ready, .failed:
        if case let .failed(detail) = store.state {
          AccessibleStatusLabel(text: detail, kind: .error).font(.caption)
        }
        Button("Start Scan", systemImage: "record.circle", action: start)
          .buttonStyle(.borderedProminent)
        Text("Close-ups are a later still after this geometry session is released. Optical zoom is not available during RoomPlan.")
          .font(.caption2)
          .foregroundStyle(.secondary)
      case .capturing:
        capturingBar
      case .paused:
        ViewThatFits {
          HStack {
            resumeButton
            stopButton
          }
          VStack {
            resumeButton
            stopButton
          }
        }
      case .processing:
        ProgressView("Processing scan")
      case .captured:
        sequentialStatus
        ViewThatFits {
          HStack {
            scanAgainButton
            reviewButton
          }
          VStack {
            scanAgainButton
            reviewButton
          }
        }
      case .unsupported:
        AccessibleStatusLabel(text: "RoomPlan is unavailable on this device.", kind: .error)
      }
    }
    .accessibilityStatusAnnouncements(captureStatus)
  }

  private var capturingBar: some View {
    ViewThatFits {
      HStack {
        pauseButton
        finishButton
      }
      VStack {
        pauseButton
        finishButton
      }
    }
  }

  private var pauseButton: some View {
    Button("Pause Scan", systemImage: "pause.fill", action: pause)
      .buttonStyle(.bordered)
      .minimumScaledTouchTarget()
  }

  private var finishButton: some View {
    Button("Finish Room Scan", systemImage: "stop.fill", action: stop)
      .buttonStyle(.borderedProminent)
      .minimumScaledTouchTarget()
  }

  private var resumeButton: some View {
    Button("Resume Scan", systemImage: "play.fill", action: resume)
      .buttonStyle(.borderedProminent)
      .minimumScaledTouchTarget()
  }

  private var stopButton: some View {
    Button("Stop Scan", systemImage: "stop.fill", action: stop)
      .buttonStyle(.bordered)
      .minimumScaledTouchTarget()
  }

  private var scanAgainButton: some View {
    Button("Scan Again", systemImage: "arrow.counterclockwise", action: reset)
      .buttonStyle(.bordered)
      .minimumScaledTouchTarget()
  }

  private var reviewButton: some View {
    Button("Photograph Shelves", systemImage: "books.vertical", action: onContinue)
      .buttonStyle(.borderedProminent)
      .minimumScaledTouchTarget()
  }

  @ViewBuilder
  private var sequentialStatus: some View {
    if let detail = store.sequentialFallbackDetail {
      Label(detail, systemImage: "camera.badge.ellipsis")
        .font(.caption)
        .foregroundStyle(.orange)
        .accessibilityLabel("Sequential fallback: \(detail)")
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

  private var captureStatus: String {
    switch store.state {
    case .ready: "Room scanner ready"
    case .capturing: "Room scan started"
    case .paused: "Room scan paused"
    case .processing: "Room scan finished. Processing geometry"
    case .captured: "Room capture complete"
    case let .failed(detail): "Room capture failed. \(detail)"
    case .unsupported: "RoomPlan is unavailable on this device"
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
