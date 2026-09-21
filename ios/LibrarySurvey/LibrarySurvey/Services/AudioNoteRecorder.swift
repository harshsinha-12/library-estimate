import AVFoundation
import Foundation

@MainActor
final class AudioNoteRecorder: NSObject, ObservableObject, AVAudioRecorderDelegate {
  @Published private(set) var isRecording = false
  @Published private(set) var recordingURL: URL?
  @Published private(set) var errorMessage: String?
  @Published private(set) var startedMonotonicSeconds: Double?
  @Published private(set) var endedMonotonicSeconds: Double?

  private var recorder: AVAudioRecorder?
  private var recordingRequestID: UUID?

  func start(in directory: URL) {
    errorMessage = nil
    recordingURL = nil
    startedMonotonicSeconds = nil
    endedMonotonicSeconds = nil
    let requestID = UUID()
    recordingRequestID = requestID
    Task { await startRecording(in: directory, requestID: requestID) }
  }

  func stop() {
    recordingRequestID = nil
    if isRecording {
      endedMonotonicSeconds = MonotonicClock.now
    }
    recorder?.stop()
    recorder = nil
    isRecording = false
    // Leave the shared session active so RoomPlan / ARKit can finish teardown.
  }

  nonisolated func audioRecorderEncodeErrorDidOccur(_ recorder: AVAudioRecorder, error: Error?) {
    let detail = error?.localizedDescription ?? "encoder failed"
    Task { @MainActor in
      self.errorMessage = self.spokenNoteMessage(detail)
      self.isRecording = false
    }
  }

  private func startRecording(in directory: URL, requestID: UUID) async {
    let granted = await AVAudioApplication.requestRecordPermission()
    guard recordingRequestID == requestID else { return }
    guard granted else {
      errorMessage = spokenNoteMessage("microphone permission was denied")
      return
    }

    do {
      try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
      let url = directory.appendingPathComponent("survey.m4a")
      if FileManager.default.fileExists(atPath: url.path) {
        try FileManager.default.removeItem(at: url)
      }

      let session = AVAudioSession.sharedInstance()
      // RoomPlan owns the session. Exclusive .record + Bluetooth HFP is OSStatus -50 (paramErr).
      try session.setCategory(
        .playAndRecord,
        mode: .videoRecording,
        options: [.mixWithOthers, .defaultToSpeaker]
      )
      try session.setActive(true)

      let sampleRate = session.sampleRate > 0 ? session.sampleRate : 48_000
      let recorder = try AVAudioRecorder(
        url: url,
        settings: [
          AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
          AVSampleRateKey: sampleRate,
          AVNumberOfChannelsKey: 1,
          AVEncoderAudioQualityKey: AVAudioQuality.medium.rawValue,
        ]
      )
      recorder.delegate = self
      guard recorder.prepareToRecord(), recorder.record() else {
        errorMessage = spokenNoteMessage("the recorder rejected the session")
        return
      }
      self.recorder = recorder
      recordingURL = url
      startedMonotonicSeconds = MonotonicClock.now
      isRecording = true
      errorMessage = nil
    } catch {
      errorMessage = spokenNoteMessage(error.localizedDescription)
    }
  }

  nonisolated private func spokenNoteMessage(_ detail: String) -> String {
    "Spoken audio unavailable (\(detail)). You can continue scanning."
  }
}
