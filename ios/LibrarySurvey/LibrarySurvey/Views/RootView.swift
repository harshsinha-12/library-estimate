import SwiftUI

struct RootView: View {
  enum Stage {
    case create
    case deviceCheck
    case roomCapture
    case shelfPhotos
    case package
  }

  @StateObject private var camera: CameraSessionCoordinator
  @StateObject private var drafts = SurveyDraftStore()
  @StateObject private var location = LocationService()
  @StateObject private var capture: RoomCaptureStore
  @StateObject private var audio = AudioNoteRecorder()
  @StateObject private var uploader = SurveyUploadService()
  @StateObject private var shelves: ShelfCaptureStore
  @StateObject private var shelfPhotos = ShelfPhotoStore()
  @State private var stage: Stage = .create
  @State private var notes: [WrittenNote] = []
  @State private var sealedPackage: SealedSurveyPackage?
  @State private var sealError: String?
  @State private var recoveryError: String?
  @State private var isRestoring = false

  init() {
    let camera = CameraSessionCoordinator()
    _camera = StateObject(wrappedValue: camera)
    _capture = StateObject(wrappedValue: RoomCaptureStore(camera: camera))
    _shelves = StateObject(wrappedValue: ShelfCaptureStore(camera: camera))
  }

  var body: some View {
    NavigationStack {
      Group {
        switch stage {
        case .create:
          CreateSurveyView(store: drafts, location: location) {
            try? drafts.save()
            stage = .deviceCheck
          }
        case .deviceCheck:
          DeviceCheckView(
            locationAvailable: location.state == .resolved || location.state == .denied
          ) {
            stage = .roomCapture
          }
        case .roomCapture:
          RoomPassView(
            store: capture,
            camera: camera,
            audio: audio,
            shelves: shelves,
            recordSpokenNotes: drafts.draft.consent.audio,
            sealError: sealError,
            onContinue: {
              capture.releaseGeometrySession()
              stage = .shelfPhotos
            }
          )
        case .shelfPhotos:
          ShelfPhotosView(
            store: shelfPhotos,
            camera: camera,
            sealError: sealError,
            onSeal: seal
          )
        case .package:
          if let sealedPackage {
            PackagePreviewView(
              package: sealedPackage,
              draft: drafts.draft,
              uploader: uploader,
              onNewSurvey: reset
            )
          }
        }
      }
      .navigationTitle(title)
      .navigationBarTitleDisplayMode(.inline)
      .accessibilityStatusAnnouncements("Opened \(title)")
      .onAppear(perform: restoreSealedPackageIfNeeded)
      .overlay {
        if isRestoring {
          ProgressView("Verifying sealed package")
            .padding()
            .background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12))
        }
      }
      .alert("Package recovery failed", isPresented: Binding(
        get: { recoveryError != nil },
        set: { if !$0 { recoveryError = nil } }
      )) {
        Button("OK", role: .cancel) { recoveryError = nil }
      } message: {
        Text(recoveryError ?? "The sealed package could not be verified.")
      }
    }
  }

  private var title: String {
    switch stage {
    case .create: "Create Survey"
    case .deviceCheck: "Device Check"
    case .roomCapture: "Room and Books Scan"
    case .shelfPhotos: "Shelf Photos"
    case .package: "Sealed Survey"
    }
  }

  private func seal() {
    audio.stop()
    shelves.releaseCamera()
    camera.release(.stillCamera)
    capture.releaseGeometrySession()
    guard let startedAt = capture.startedAt,
          let monotonicAnchor = capture.monotonicAnchor
    else {
      sealError = "Start and finish a RoomPlan scan before sealing."
      return
    }
    Task {
      do {
        let package = try await CapturePackageWriter.seal(
          draft: drafts.draft,
          rooms: capture.rooms,
          samples: capture.samples,
          notes: notes,
          audioURL: audio.recordingURL,
          audioStartedMonotonicSeconds: audio.startedMonotonicSeconds,
          audioEndedMonotonicSeconds: audio.endedMonotonicSeconds,
          startedAt: startedAt,
          monotonicAnchor: monotonicAnchor,
          shelfPhotos: shelfPhotos.export(),
          rgbEvidenceMode: capture.rgbEvidenceMode.rawValue
        )
        sealedPackage = package
        capture.releaseAfterSeal()
        camera.reset()
        sealError = nil
        stage = .package
      } catch {
        sealError = error.localizedDescription
      }
    }
  }

  private func restoreSealedPackageIfNeeded() {
    guard stage == .create, sealedPackage == nil, !isRestoring else { return }
    let surveyId = drafts.draft.id
    isRestoring = true
    Task {
      do {
        let package = try await Task.detached(priority: .userInitiated) {
          try CapturePackageWriter.loadExisting(surveyId: surveyId)
        }.value
        if let package, drafts.draft.id == surveyId {
          sealedPackage = package
          stage = .package
        }
      } catch {
        recoveryError = error.localizedDescription
      }
      isRestoring = false
    }
  }

  private func reset() {
    capture.reset()
    camera.reset()
    drafts.reset()
    notes = []
    sealedPackage = nil
    sealError = nil
    stage = .create
    shelves.captures = []
    shelves.releaseCamera()
    shelfPhotos.reset()
    shelves.units = [ShelfUnit(id: UUID(), name: "Shelf 1", rowCount: 3, roomName: "Library")]
  }
}
