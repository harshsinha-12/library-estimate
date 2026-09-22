import Foundation
import RoomPlan
import UIKit

enum PackageWriterError: LocalizedError {
  case noRooms
  case invalidGeometry(String)
  case invalidAudioTiming
  case verificationFailed(String)

  var errorDescription: String? {
    switch self {
    case .noRooms: "Scan at least one room before sealing."
    case let .invalidGeometry(message): message
    case .invalidAudioTiming: "Recorded audio has no valid capture-clock interval. Scan again."
    case let .verificationFailed(path): "Hash verification failed for \(path)."
    }
  }
}

@MainActor
enum CapturePackageWriter {
  static func seal(
    draft: SurveyDraft,
    rooms: [CapturedRoom],
    samples: [FrameSample],
    notes: [WrittenNote],
    audioURL: URL?,
    audioStartedMonotonicSeconds: Double?,
    audioEndedMonotonicSeconds: Double?,
    startedAt: Date,
    monotonicAnchor: Double,
    shelfPackage: LabeledShelfPackage = LabeledShelfPackage(passes: []),
    shelfFrames: [FrameSample] = [],
    exceptionPackage: PassCPackage = PassCPackage(scans: [], notes: [], focusEvents: []),
    otherAssets: [OtherAssetMark] = [],
    exceptionImages: [String: Data] = [:],
    shelfCrops: [String: Data] = [:],
    faceRedactionEnabled: Bool = AppConfiguration.faceRedactionRequired,
    rgbEvidenceMode: String = CameraSessionCoordinator.RGBEvidenceMode.sampledDuringScan.rawValue,
    shelfFootprintPlacement: String = "unregistered_overlay",
    fileManager: FileManager = .default
  ) async throws -> SealedSurveyPackage {
    guard let firstRoom = rooms.first else { throw PackageWriterError.noRooms }
    let root = try packageRoot(surveyId: draft.id, fileManager: fileManager)
    let structure = try await RoomPlanAdapter.portableStructure(
      from: rooms,
      defaultLabel: "Library"
    )
    var files: [PackageFile] = []
    var redactionRecords: [EvidenceRedactionRecord] = []

    try writeJSON(
      structure,
      relativePath: "roomplan/raw/room-data.json",
      mimeType: "application/json",
      root: root,
      files: &files,
      fileManager: fileManager
    )
    try writeJSON(
      structure,
      relativePath: "roomplan/processed/structure.json",
      mimeType: "application/json",
      root: root,
      files: &files,
      fileManager: fileManager
    )

    let usdzURL = root.appendingPathComponent("roomplan/model.usdz")
    try createParent(of: usdzURL, fileManager: fileManager)
    try firstRoom.export(to: usdzURL)
    try appendFile(usdzURL, relativePath: "roomplan/model.usdz", mimeType: "model/vnd.usdz+zip", files: &files)

    let svgURL = root.appendingPathComponent("generated/plan.svg")
    try createParent(of: svgURL, fileManager: fileManager)
    try Data(RoomPlanSVGRenderer.render(structure).utf8).write(to: svgURL, options: .atomic)
    try appendFile(svgURL, relativePath: "generated/plan.svg", mimeType: "image/svg+xml", files: &files)

    struct PropertyRecord: Codable {
      let propertyId: String
      let roomIds: [String]
    }
    try writeJSON(
      PropertyRecord(
        propertyId: draft.id.uuidString,
        roomIds: structure.rooms.map(\.identifier)
      ),
      relativePath: "property.json",
      mimeType: "application/json",
      root: root,
      files: &files,
      fileManager: fileManager
    )
    try writeJSON(
      draft.geography,
      relativePath: "device/location.json",
      mimeType: "application/json",
      root: root,
      files: &files,
      fileManager: fileManager
    )
    try writeJSON(
      [
        "coordinate_system": "roomplan-world",
        "units": "metres",
        "camera_ownership": "sequential",
        "rgb_evidence_mode": rgbEvidenceMode,
        "shelf_footprints": shelfFootprintPlacement
      ],
      relativePath: "device/calibration.json",
      mimeType: "application/json",
      root: root,
      files: &files,
      fileManager: fileManager
    )
    try writeFrames(
      samples, root: root, files: &files, redactionRecords: &redactionRecords,
      faceRedactionEnabled: faceRedactionEnabled, fileManager: fileManager
    )
    try writeShelfScans(
      package: shelfPackage,
      frames: shelfFrames,
      crops: shelfCrops,
      root: root,
      files: &files,
      redactionRecords: &redactionRecords,
      faceRedactionEnabled: faceRedactionEnabled,
      fileManager: fileManager
    )
    if !exceptionPackage.scans.isEmpty || !exceptionPackage.notes.isEmpty || !exceptionPackage.focusEvents.isEmpty {
      try writeJSON(exceptionPackage, relativePath: "exceptions/pass-c.json", mimeType: "application/json",
                    root: root, files: &files, fileManager: fileManager)
    }
    if !otherAssets.isEmpty {
      try writeJSON(otherAssets, relativePath: "other_assets/marks.json", mimeType: "application/json",
                    root: root, files: &files, fileManager: fileManager)
    }
    for (path, bytes) in exceptionImages.sorted(by: { $0.key < $1.key }) {
      guard path.hasPrefix("closeups/"), !path.contains("..") else { continue }
      try writeJPEG(
        bytes, relativePath: path, root: root, files: &files,
        redactionRecords: &redactionRecords, faceRedactionEnabled: faceRedactionEnabled,
        fileManager: fileManager
      )
    }
    try writeJSON(
      notes,
      relativePath: "notes/annotations.json",
      mimeType: "application/json",
      root: root,
      files: &files,
      fileManager: fileManager
    )

    if let audioURL, fileManager.fileExists(atPath: audioURL.path) {
      guard let audioStartedMonotonicSeconds, let audioEndedMonotonicSeconds,
            audioEndedMonotonicSeconds >= audioStartedMonotonicSeconds
      else { throw PackageWriterError.invalidAudioTiming }
      let target = root.appendingPathComponent("audio/survey.m4a")
      try createParent(of: target, fileManager: fileManager)
      if fileManager.fileExists(atPath: target.path) { try fileManager.removeItem(at: target) }
      try fileManager.copyItem(at: audioURL, to: target)
      try appendFile(target, relativePath: "audio/survey.m4a", mimeType: "audio/mp4", files: &files)
      struct AudioTiming: Codable {
        let audioPath: String
        let startedMonotonicSeconds: Double
        let endedMonotonicSeconds: Double
      }
      try writeJSON(
        AudioTiming(
          audioPath: "audio/survey.m4a",
          startedMonotonicSeconds: audioStartedMonotonicSeconds,
          endedMonotonicSeconds: audioEndedMonotonicSeconds
        ),
        relativePath: "audio/timing.json",
        mimeType: "application/json",
        root: root,
        files: &files,
        fileManager: fileManager
      )
    }

    try writeJSON(
      RedactionMetadata(
        schemaVersion: "1",
        policy: faceRedactionEnabled ? "required" : "disabled",
        failClosed: faceRedactionEnabled,
        records: redactionRecords
      ),
      relativePath: "privacy/face-redaction.json",
      mimeType: "application/json",
      root: root,
      files: &files,
      fileManager: fileManager
    )

    let checksums = files
      .sorted { $0.path < $1.path }
      .map { "\($0.sha256)  \($0.path)" }
      .joined(separator: "\n") + "\n"
    let checksumsURL = root.appendingPathComponent("checksums.sha256")
    try Data(checksums.utf8).write(to: checksumsURL, options: .atomic)
    try appendFile(checksumsURL, relativePath: "checksums.sha256", mimeType: "text/plain", files: &files)

    let manifest = CaptureManifest(
      schemaVersion: AppConfiguration.schemaVersion,
      surveyId: draft.id,
      sessionId: UUID(),
      app: .init(version: AppConfiguration.appVersion, build: AppConfiguration.appBuild),
      device: .init(
        model: UIDevice.current.model,
        systemVersion: UIDevice.current.systemVersion,
        supportsLidar: RoomCaptureSession.isSupported,
        roomplanVersion: UIDevice.current.systemVersion,
        visionVersion: UIDevice.current.systemVersion
      ),
      geography: draft.geography,
      consent: draft.consent,
      timing: .init(
        startedAt: startedAt,
        endedAt: Date(),
        monotonicAnchorSeconds: monotonicAnchor,
        timezone: TimeZone.current.identifier
      ),
      captureState: "complete",
      captureModes: (shelfPackage.passes.isEmpty ? ["room"] : ["room", "shelf"]) +
        ((!exceptionPackage.scans.isEmpty || !otherAssets.isEmpty || !exceptionPackage.focusEvents.isEmpty) ? ["exception"] : []),
      files: files
    )
    let manifestURL = root.appendingPathComponent("manifest.json")
    try JSONCoding.encoder().encode(manifest).write(to: manifestURL, options: .atomic)
    try PackageStorageSecurity.secureTree(root, fileManager: fileManager)
    try verify(manifest: manifest, root: root)
    return SealedSurveyPackage(
      surveyId: draft.id,
      rootURL: root,
      manifestURL: manifestURL,
      svgURL: svgURL,
      usdzURL: usdzURL,
      manifest: manifest
    )
  }

  nonisolated static func loadExisting(
    surveyId: UUID,
    fileManager: FileManager = .default
  ) throws -> SealedSurveyPackage? {
    let support = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
    let root = support
      .appendingPathComponent("LibrarySurvey/Packages", isDirectory: true)
      .appendingPathComponent("survey_\(surveyId.uuidString)", isDirectory: true)
    var isDirectory: ObjCBool = false
    guard fileManager.fileExists(atPath: root.path, isDirectory: &isDirectory),
          isDirectory.boolValue
    else { return nil }
    let manifestURL = root.appendingPathComponent("manifest.json")
    let data = try Data(contentsOf: manifestURL)
    let manifest = try JSONCoding.decoder().decode(CaptureManifest.self, from: data)
    guard manifest.surveyId == surveyId else {
      throw PackageWriterError.verificationFailed("manifest.json: survey ID")
    }
    try PackageStorageSecurity.secureTree(root, fileManager: fileManager)
    try verify(manifest: manifest, root: root)
    let svgURL = root.appendingPathComponent("generated/plan.svg")
    let usdzURL = root.appendingPathComponent("roomplan/model.usdz")
    guard fileManager.fileExists(atPath: svgURL.path) else {
      throw PackageWriterError.verificationFailed("generated/plan.svg")
    }
    guard fileManager.fileExists(atPath: usdzURL.path) else {
      throw PackageWriterError.verificationFailed("roomplan/model.usdz")
    }
    return SealedSurveyPackage(
      surveyId: surveyId,
      rootURL: root,
      manifestURL: manifestURL,
      svgURL: svgURL,
      usdzURL: usdzURL,
      manifest: manifest
    )
  }

  nonisolated static func verify(manifest: CaptureManifest, root: URL) throws {
    for file in manifest.files {
      let url = root.appendingPathComponent(file.path)
      let size = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? NSNumber)?
        .intValue
      let digest = try? FileHashing.sha256(fileAt: url)
      guard size == file.bytes, digest == file.sha256 else {
        throw PackageWriterError.verificationFailed(file.path)
      }
    }
  }

  private static func writeShelfScans(
    package: LabeledShelfPackage,
    frames: [FrameSample],
    crops: [String: Data],
    root: URL,
    files: inout [PackageFile],
    redactionRecords: inout [EvidenceRedactionRecord],
    faceRedactionEnabled: Bool,
    fileManager: FileManager
  ) throws {
    guard !package.passes.isEmpty else { return }
    try writeJSON(
      package,
      relativePath: "shelf_scans/labeled.json",
      mimeType: "application/json",
      root: root,
      files: &files,
      fileManager: fileManager
    )
    struct FaceQuality: Codable {
      let faceId: String
      let blur: Double
      let glare: Double
      let speed: Double
      let textPixelHeight: Double
      let occlusion: Double
    }
    let snapshot = package.passes.map {
      FaceQuality(
        faceId: $0.faceId,
        blur: $0.quality.blur,
        glare: $0.quality.glare,
        speed: $0.quality.speed,
        textPixelHeight: $0.quality.textPixelHeight,
        occlusion: $0.quality.occlusion
      )
    }
    try writeJSON(
      snapshot,
      relativePath: "shelf_scans/quality.json",
      mimeType: "application/json",
      root: root,
      files: &files,
      fileManager: fileManager
    )
    for (index, sample) in frames.enumerated() {
      let path = String(format: "shelf_scans/frames/%04d.jpg", index + 1)
      try writeJPEG(
        sample.jpegData, relativePath: path, root: root, files: &files,
        redactionRecords: &redactionRecords, faceRedactionEnabled: faceRedactionEnabled,
        fileManager: fileManager
      )
    }
    for (path, bytes) in crops.sorted(by: { $0.key < $1.key }) {
      guard path.hasPrefix("shelf_scans/crops/"), path.lowercased().hasSuffix(".jpg") else { continue }
      try writeJPEG(
        bytes, relativePath: path, root: root, files: &files,
        redactionRecords: &redactionRecords, faceRedactionEnabled: faceRedactionEnabled,
        fileManager: fileManager
      )
    }
  }

  private static func writeFrames(
    _ samples: [FrameSample],
    root: URL,
    files: inout [PackageFile],
    redactionRecords: inout [EvidenceRedactionRecord],
    faceRedactionEnabled: Bool,
    fileManager: FileManager
  ) throws {
    struct Pose: Codable {
      let frameId: UUID
      let capturedAt: Date
      let monotonicSeconds: Double
      let cameraTransform: [Float]
      let imagePath: String
    }
    var poses: [Pose] = []
    for (index, sample) in samples.enumerated() {
      let path = String(format: "roomplan/raw/frames/%04d.jpg", index + 1)
      try writeJPEG(
        sample.jpegData, relativePath: path, root: root, files: &files,
        redactionRecords: &redactionRecords, faceRedactionEnabled: faceRedactionEnabled,
        fileManager: fileManager
      )
      poses.append(
        Pose(
          frameId: sample.id,
          capturedAt: sample.capturedAt,
          monotonicSeconds: sample.monotonicSeconds,
          cameraTransform: sample.cameraTransform,
          imagePath: path
        )
      )
    }
    try writeJSON(
      poses,
      relativePath: "roomplan/raw/poses.json",
      mimeType: "application/json",
      root: root,
      files: &files,
      fileManager: fileManager
    )
  }

  private static func writeJSON<T: Encodable>(
    _ value: T,
    relativePath: String,
    mimeType: String,
    root: URL,
    files: inout [PackageFile],
    fileManager: FileManager
  ) throws {
    let url = root.appendingPathComponent(relativePath)
    try createParent(of: url, fileManager: fileManager)
    try JSONCoding.encoder().encode(value).write(to: url, options: .atomic)
    try appendFile(url, relativePath: relativePath, mimeType: mimeType, files: &files)
  }

  private struct RedactionMetadata: Encodable {
    let schemaVersion: String
    let policy: String
    let failClosed: Bool
    let records: [EvidenceRedactionRecord]

    enum CodingKeys: String, CodingKey {
      case schemaVersion = "schema_version"
      case policy
      case failClosed = "fail_closed"
      case records
    }
  }

  private static func writeJPEG(
    _ data: Data,
    relativePath: String,
    root: URL,
    files: inout [PackageFile],
    redactionRecords: inout [EvidenceRedactionRecord],
    faceRedactionEnabled: Bool,
    fileManager: FileManager
  ) throws {
    let processed = try EvidenceFaceRedactor.process(
      jpeg: data, path: relativePath, enabled: faceRedactionEnabled
    )
    let url = root.appendingPathComponent(relativePath)
    try createParent(of: url, fileManager: fileManager)
    try processed.data.write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    try PackageStorageSecurity.secureFile(url, fileManager: fileManager)
    redactionRecords.append(processed.record)
    try appendFile(url, relativePath: relativePath, mimeType: "image/jpeg", files: &files)
  }

  private static func appendFile(
    _ url: URL,
    relativePath: String,
    mimeType: String,
    files: inout [PackageFile]
  ) throws {
    try PackageStorageSecurity.secureFile(url)
    let bytes = (try FileManager.default.attributesOfItem(atPath: url.path)[.size] as? NSNumber)?
      .intValue ?? 0
    files.append(
      PackageFile(
        path: relativePath,
        mimeType: mimeType,
        bytes: bytes,
        sha256: try FileHashing.sha256(fileAt: url)
      )
    )
  }

  private static func packageRoot(surveyId: UUID, fileManager: FileManager) throws -> URL {
    let support = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
    let root = support
      .appendingPathComponent("LibrarySurvey/Packages", isDirectory: true)
      .appendingPathComponent("survey_\(surveyId.uuidString)", isDirectory: true)
    try fileManager.createDirectory(at: root, withIntermediateDirectories: true)
    try PackageStorageSecurity.secureDirectory(root, fileManager: fileManager)
    return root
  }

  private static func createParent(of url: URL, fileManager: FileManager) throws {
    try fileManager.createDirectory(
      at: url.deletingLastPathComponent(),
      withIntermediateDirectories: true
    )
    try PackageStorageSecurity.secureDirectory(
      url.deletingLastPathComponent(), fileManager: fileManager
    )
  }
}
