import Foundation
import UIKit
import Vision

struct LivePriceResponse: Decodable, Identifiable {
  var id: String { query ?? title ?? UUID().uuidString }
  let status: String
  let reason: String?
  let title: String?
  let isbn: String?
  let query: String?
  let queryKind: String?
  let amount: String?
  let currency: String?
  let listingUrl: String?
  let listingCount: Int?
  let parser: String?
  let smallModel: String?
}

struct BookIdentity {
  var isbn: String?
  var barcode: String?
  var title: String?
  var ocrText: String
  var box: CGRect?
}

struct LiveBookHighlight: Identifiable {
  var id: String { key }
  let key: String
  var box: CGRect
  var title: String?
  var status: String
  var amount: String?
  var currency: String?

  var caption: String {
    if status == "draft", let amount, let currency {
      return "\(title ?? "Book") · \(currency) \(amount)"
    }
    if let title { return title }
    return "Book"
  }
}

enum LivePriceAssist {
  private static let publishers = [
    "isbn", "issn", "price", "www.", "http", "o'reilly", "oreilly", "packt", "wiley", "manning"
  ]

  static func identities(from jpeg: Data) -> [BookIdentity] {
    guard let image = UIImage(data: jpeg)?.cgImage else { return [] }
    let barcodes = VNDetectBarcodesRequest()
    let text = VNRecognizeTextRequest()
    text.recognitionLevel = .accurate
    try? VNImageRequestHandler(cgImage: image).perform([barcodes, text])
    let barcode = barcodes.results?.compactMap(\.payloadStringValue).first
    let lines = (text.results ?? []).compactMap { $0.topCandidates(1).first?.string }
    let ocr = lines.joined(separator: "\n")
    let isbn = barcode.flatMap(Self.isbn(from:))
    var items: [BookIdentity] = []
    var seen = Set<String>()
    if let isbn {
      items.append(BookIdentity(isbn: isbn, barcode: barcode, title: nil, ocrText: ocr, box: nil))
      seen.insert(isbn)
    }
    for box in LiveQualityAnalyzer.spineRegions(jpeg: jpeg).prefix(8) {
      guard let crop = crop(image, box: box) else { continue }
      let region = VNRecognizeTextRequest()
      region.recognitionLevel = .accurate
      try? VNImageRequestHandler(cgImage: crop).perform([region])
      let regionText = (region.results ?? [])
        .compactMap { $0.topCandidates(1).first?.string }
        .joined(separator: "\n")
      let name = titles(from: regionText).first ?? titles(from: ocr).first
      let key = (name ?? regionText).lowercased()
      if key.count < 8 || seen.contains(key) { continue }
      seen.insert(key)
      items.append(
        BookIdentity(isbn: nil, barcode: barcode, title: name, ocrText: regionText, box: box)
      )
    }
    if items.isEmpty {
      for name in titles(from: ocr) {
        let key = name.lowercased()
        if seen.contains(key) { continue }
        seen.insert(key)
        items.append(BookIdentity(isbn: nil, barcode: barcode, title: name, ocrText: ocr, box: nil))
      }
    }
    if items.isEmpty, ocr.count >= 8 {
      items.append(
        BookIdentity(isbn: isbn, barcode: barcode, title: title(from: ocr), ocrText: ocr, box: nil)
      )
    }
    return Array(items.prefix(8))
  }

  static func identity(from jpeg: Data) -> BookIdentity? {
    identities(from: jpeg).first
  }

  static func isbn(from raw: String) -> String? {
    let digits = raw.filter(\.isNumber)
    if digits.count == 13 && (digits.hasPrefix("978") || digits.hasPrefix("979")) {
      return digits
    }
    return nil
  }

  static func titles(from ocr: String) -> [String] {
    let lines = ocr.split(whereSeparator: \.isNewline).compactMap { raw -> String? in
      let line = raw.trimmingCharacters(in: .whitespacesAndNewlines)
      guard line.count >= 3 else { return nil }
      let lower = line.lowercased()
      if publishers.contains(where: { lower.hasPrefix($0) }) { return nil }
      let letters = line.filter(\.isLetter)
      return letters.count >= 3 ? line : nil
    }
    var found: [String] = []
    var stacked: [String] = []

    func flush() {
      let title = stacked.joined(separator: " ")
      stacked.removeAll()
      let key = title.lowercased()
      if title.count >= 8, !found.contains(where: { $0.lowercased() == key }) {
        found.append(String(title.prefix(160)))
      }
    }

    for line in lines {
      let words = line.split(whereSeparator: \.isWhitespace)
      if line.count > 42 || words.count >= 4 {
        if !stacked.isEmpty { flush() }
        let key = line.lowercased()
        if !found.contains(where: { $0.lowercased() == key }) {
          found.append(String(line.prefix(160)))
        }
        continue
      }
      stacked.append(line)
      if stacked.count >= 4 { flush() }
    }
    if !stacked.isEmpty { flush() }
    if found.isEmpty {
      let compact = ocr.split(whereSeparator: \.isWhitespace).joined(separator: " ")
      if compact.count >= 8 { found.append(String(compact.prefix(160))) }
    }
    return Array(found.prefix(8))
  }

  static func title(from ocr: String) -> String? {
    titles(from: ocr).first
  }

  private static func crop(_ image: CGImage, box: CGRect) -> CGImage? {
    let width = CGFloat(image.width)
    let height = CGFloat(image.height)
    let rect = CGRect(
      x: box.minX * width,
      y: (1 - box.maxY) * height,
      width: max(1, box.width * width),
      height: max(1, box.height * height)
    ).integral
    return image.cropping(to: rect)
  }
}

@MainActor
final class LivePriceSession: ObservableObject {
  @Published var latest: LivePriceResponse?
  @Published var status = ""
  @Published var highlights: [LiveBookHighlight] = []
  private var searched = Set<String>()
  private var lastAttempt = Date.distantPast
  private var prepared = false

  func highlight(for box: CGRect) -> LiveBookHighlight? {
    highlights.min { left, right in
      hypot(left.box.midX - box.midX, left.box.midY - box.midY)
        < hypot(right.box.midX - box.midX, right.box.midY - box.midY)
    }.flatMap { candidate in
      hypot(candidate.box.midX - box.midX, candidate.box.midY - box.midY) < 0.12 ? candidate : nil
    }
  }

  func consider(jpeg: Data, draft: SurveyDraft, backendURL: URL?) async {
    guard let backendURL, Date().timeIntervalSince(lastAttempt) >= 1.6 else { return }
    lastAttempt = Date()
    let identities = LivePriceAssist.identities(from: jpeg)
    guard !identities.isEmpty else { return }
    do {
      try await prepare(draft: draft, backendURL: backendURL)
    } catch {
      status = "Price search paused. Keep scanning, then try again."
      return
    }
    for identity in identities.prefix(4) {
      let key = (identity.isbn ?? identity.title ?? identity.ocrText).lowercased()
      guard !key.isEmpty else { continue }
      if let box = identity.box, !highlights.contains(where: { $0.key == key }) {
        highlights.append(
          LiveBookHighlight(
            key: key, box: box, title: identity.title, status: "searching",
            amount: nil, currency: nil
          )
        )
        if highlights.count > 12 { highlights.removeFirst(highlights.count - 12) }
      }
      guard !searched.contains(key) else { continue }
      searched.insert(key)
      do {
        let result = try await search(identity: identity, draft: draft, backendURL: backendURL)
        latest = result
        if let index = highlights.firstIndex(where: { $0.key == key }) {
          highlights[index].title = result.title ?? identity.title
          highlights[index].status = result.status
          highlights[index].amount = result.amount
          highlights[index].currency = result.currency
        }
        if result.status == "draft", let amount = result.amount {
          status = "\(result.title ?? "Book") · \(result.currency ?? "") \(amount) draft"
        }
      } catch {
        searched.remove(key)
        status = "Price search paused. Keep scanning, then try again."
      }
    }
  }

  func considerCapturedText(
    title: String,
    barcode: String,
    ocr: String,
    draft: SurveyDraft,
    backendURL: URL?
  ) async {
    guard let backendURL else { return }
    var identities: [BookIdentity] = []
    let isbn = LivePriceAssist.isbn(from: barcode)
    if !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
      identities.append(
          BookIdentity(
            isbn: isbn,
            barcode: barcode.isEmpty ? nil : barcode,
            title: title,
            ocrText: ocr,
            box: nil
          )
      )
    } else {
      identities = LivePriceAssist.titles(from: ocr).map { name in
        BookIdentity(
          isbn: isbn,
          barcode: barcode.isEmpty ? nil : barcode,
          title: name,
          ocrText: ocr,
          box: nil
        )
      }
      if identities.isEmpty {
        identities.append(
          BookIdentity(
            isbn: isbn,
            barcode: barcode.isEmpty ? nil : barcode,
            title: LivePriceAssist.title(from: ocr),
            ocrText: ocr,
            box: nil
          )
        )
      }
    }
    for identity in identities.prefix(4) {
      let key = (identity.isbn ?? identity.title ?? identity.ocrText).lowercased()
      guard !key.isEmpty, !searched.contains(key) else { continue }
      searched.insert(key)
      do {
        try await prepare(draft: draft, backendURL: backendURL)
        latest = try await search(identity: identity, draft: draft, backendURL: backendURL)
        if latest?.status == "draft", let amount = latest?.amount {
          status = "\(latest?.title ?? "Book") · \(latest?.currency ?? "") \(amount) draft"
        }
      } catch {
        searched.remove(key)
        status = "Price search paused. Scan again."
      }
    }
  }

  func considerObject(
    category: String,
    label: String,
    spoken: String,
    jpeg: Data?,
    assetCopyId: String?,
    draft: SurveyDraft,
    backendURL: URL?
  ) async {
    guard let backendURL else { return }
    let key = "object:\(category):\(label):\(spoken)".lowercased()
    guard !searched.contains(key) else { return }
    searched.insert(key)
    do {
      try await prepare(draft: draft, backendURL: backendURL)
      let url = backendURL.appendingPathComponent(
        "v1/surveys/\(draft.id.uuidString)/live-price-search"
      )
      var request = URLRequest(url: url)
      request.httpMethod = "POST"
      request.timeoutInterval = 60
      request.setValue("application/json", forHTTPHeaderField: "Content-Type")
      request.httpBody = try JSONCoding.encoder(pretty: false).encode(
        LivePricePayload(
          isbn: nil,
          title: label.isEmpty ? nil : label,
          barcode: nil,
          ocrText: spoken,
          category: category,
          spokenText: spoken,
          imageBase64: jpeg?.base64EncodedString(),
          assetCopyId: assetCopyId
        )
      )
      let (data, response) = try await OperatorSession.data(for: request)
      guard (response as? HTTPURLResponse)?.statusCode == 200 else {
        throw URLError(.badServerResponse)
      }
      latest = try JSONCoding.decoder().decode(LivePriceResponse.self, from: data)
      if latest?.status == "draft", let amount = latest?.amount {
        status = "\(latest?.title ?? label) · \(latest?.currency ?? "") \(amount) draft"
      }
    } catch {
      searched.remove(key)
      status = "Object price search paused. Capture again."
    }
  }

  private func prepare(draft: SurveyDraft, backendURL: URL) async throws {
    if prepared { return }
    var request = URLRequest(url: backendURL.appendingPathComponent("v1/surveys"))
    request.httpMethod = "POST"
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.setValue("create-\(draft.id.uuidString)", forHTTPHeaderField: "Idempotency-Key")
    request.httpBody = try JSONCoding.encoder(pretty: false).encode(
      SurveyCreatePayload(
        surveyId: draft.id,
        displayName: draft.displayName.isEmpty ? "Live survey" : draft.displayName,
        geography: draft.geography
      )
    )
    let (_, response) = try await OperatorSession.data(for: request)
    let code = (response as? HTTPURLResponse)?.statusCode ?? 0
    guard (200..<300).contains(code) || code == 409 else {
      throw URLError(.badServerResponse)
    }
    prepared = true
  }

  private func search(
    identity: BookIdentity,
    draft: SurveyDraft,
    backendURL: URL
  ) async throws -> LivePriceResponse {
    let url = backendURL.appendingPathComponent(
      "v1/surveys/\(draft.id.uuidString)/live-price-search"
    )
    var request = URLRequest(url: url)
    request.httpMethod = "POST"
    request.timeoutInterval = 45
    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
    request.httpBody = try JSONCoding.encoder(pretty: false).encode(
      LivePricePayload(
        isbn: identity.isbn,
        title: identity.title,
        barcode: identity.barcode,
        ocrText: identity.ocrText,
        category: nil,
        spokenText: nil,
        imageBase64: nil,
        assetCopyId: nil
      )
    )
    let (data, response) = try await OperatorSession.data(for: request)
    guard (response as? HTTPURLResponse)?.statusCode == 200 else {
      throw URLError(.badServerResponse)
    }
    return try JSONCoding.decoder().decode(LivePriceResponse.self, from: data)
  }
}

private struct SurveyCreatePayload: Encodable {
  let surveyId: UUID
  let displayName: String
  let geography: SurveyGeography
}

private struct LivePricePayload: Encodable {
  let isbn: String?
  let title: String?
  let barcode: String?
  let ocrText: String
  let category: String?
  let spokenText: String?
  let imageBase64: String?
  let assetCopyId: String?
}
