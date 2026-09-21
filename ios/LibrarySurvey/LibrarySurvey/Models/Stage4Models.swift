import Foundation

struct Stage4Fraction: Decodable {
  let numerator: Int
  let denominator: Int?
}

struct Stage4Measurement: Decodable {
  let value: Double
  let unit: String?
  let status: String?
  let interval: Stage4Interval?
}

struct Stage4Interval: Decodable {
  let low: Double
  let high: Double
}

struct Stage4Contents: Decodable {
  let currency: String
  let low: Double?
  let central: Double?
  let high: Double?
  let status: String
  let note: String?
  let confirmedCopies: Int?
}

struct Stage4Building: Decodable {
  let basis: String
  let status: String
  let rateTableVersion: String?
  let disclaimer: String?
  let currency: String?
  let floorArea: Stage4Measurement?
  let amount: Stage4Measurement?
  let notMarketValue: Bool?
}

struct Stage4LedgerLine: Decodable, Identifiable {
  var id: String { "\(kind)-\(occurredAt)-\(query)" }
  let kind: String
  let query: String
  let market: String
  let cached: Bool
  let occurredAt: String
}

struct Stage4Ledger: Decodable {
  let currency: String?
  let lines: [Stage4LedgerLine]
}

struct Stage4Copy: Decodable, Identifiable {
  var id: String { assetCopyId }
  let assetCopyId: String
  let category: String
  let label: String?
  let slot: Int?
  let faceId: String?
  let rowId: String?
  let isbn: String?
  let title: String?
  let eligible: Bool
  let excluded: Bool
  let requiresAppraisal: Bool
  let query: String?
  let queryKind: String?
  let identityTask: String?
  let identityStatus: String
  let valuationStatus: String
  let reason: String
  let draftCount: Int
  let confirmedCount: Int
  let listingUrl: String?
  let actions: [String]
}

struct Stage4Row: Decodable, Identifiable {
  var id: String { "\(faceId ?? "none")/\(rowId ?? "none")" }
  let faceId: String?
  let rowId: String?
  let coverage: Double?
  let coverageStatus: String?
  let detectedCount: Int
  let actualCount: Int?
  let detectedActual: Stage4Fraction
  let recapture: Bool
  let copies: [Stage4Copy]
}

struct Stage4Overview: Decodable {
  let surveyId: UUID
  let displayName: String
  let cityMarket: String
  let copyCount: Int
  let editionCount: Int
  let eligibleCount: Int
  let pricedCount: Int
  let pricedEligible: Stage4Fraction
  let unresolvedCount: Int
  let contents: Stage4Contents
  let building: Stage4Building
  let recapture: [String]
  let rows: [Stage4Row]
  let copies: [Stage4Copy]
  let ledger: Stage4Ledger
}

struct Stage4Draft: Decodable, Identifiable {
  var id: String { priceObservationId }
  let priceObservationId: String
  let title: String?
  let snippet: String?
  let sourceUrl: String
  let offerType: String
  let parsedAmount: String?
  let currency: String?
  let reviewStatus: String
}

struct Stage4Search: Decodable {
  let query: String
  let queryKind: String?
  let market: String
  let listingUrl: String
  let citations: [Stage4Citation]
  let parser: String?
}

struct Stage4Citation: Decodable, Identifiable {
  var id: String { url }
  let title: String
  let url: String
  let snippet: String
  let offerType: String
  let parsedAmount: String?
}

struct Stage4SearchResponse: Decodable {
  let assetCopyId: String
  let status: String
  let reason: String?
  let search: Stage4Search?
  let drafts: [Stage4Draft]?
}
