import Foundation

@MainActor
final class SurveyDraftStore: ObservableObject {
  @Published var draft: SurveyDraft

  private let fileURL: URL

  init(fileManager: FileManager = .default) {
    let support = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
    let directory = support.appendingPathComponent("LibrarySurvey", isDirectory: true)
    try? fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
    fileURL = directory.appendingPathComponent("active-survey.json")
    draft = (try? Data(contentsOf: fileURL)).flatMap {
      try? JSONCoding.decoder().decode(SurveyDraft.self, from: $0)
    } ?? .empty()
  }

  func save() throws {
    try JSONCoding.encoder().encode(draft).write(to: fileURL, options: .atomic)
  }

  func reset() {
    draft = .empty()
    try? save()
  }
}

