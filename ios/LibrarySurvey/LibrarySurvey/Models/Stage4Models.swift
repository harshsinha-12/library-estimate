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
  let evidencePaths: [String]?
  let actions: [String]
  let condition: String?
  let valuation: Stage4Valuation?
  let countEvidence: [Stage4CountEvidence]?
  let damageEvidence: [Stage4DamageEvidence]?
  let spokenNotes: [Stage4SpokenNote]?
  let reviewTasks: [Stage4ReviewTask]?
  let confirmedPriceEvidence: [Stage4PriceEvidence]?
  let placement: String?
}

struct Stage4Valuation: Decodable {
  let amount: Stage4Measurement
}

struct Stage4CountEvidence: Decodable, Identifiable {
  var id: String { observationId }
  let observationId: String
  let evidenceRef: String?
  let faceId: String?
  let rowId: String?
  let slot: Int?
  let x: Double?
  let readable: Bool?
  let stacked: Bool?
  let leaning: Bool?
}

struct Stage4DamageEvidence: Decodable, Identifiable {
  var id: String { damageId }
  let damageId: String
  let type: String
  let severityCandidate: String?
  let region: String?
  let closeupRef: String?
  let scaleRef: String?
  let status: String?
}

struct Stage4SpokenNote: Decodable, Identifiable {
  var id: String { noteId }
  let noteId: String
  let text: String
  let status: String?
  let associationMethod: String?
  let closeupRef: String?
}

struct Stage4ReviewTask: Decodable, Identifiable {
  let id: String
  let kind: String
  let message: String
  let status: String
}

struct Stage4PriceEvidence: Decodable, Identifiable {
  var id: String { priceObservationId }
  let priceObservationId: String
  let sourceUrl: String?
  let title: String?
  let snippet: String?
  let parsedAmount: String?
  let currency: String?
  let evidenceHash: String?
  let reviewStatus: String
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
  let countInterval: Stage4Interval?
  let placement: String?
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

struct ModelReplayList: Decodable {
  let runs: [ModelReplayRun]
}

struct ModelReplayRun: Decodable, Identifiable {
  var id: String { runId.uuidString }
  let runId: UUID
  let surveyId: UUID
  let assetCopyId: String
  let assessments: ModelReplayAssessments?
  let jev: ModelReplayJev?
  let comparison: ModelReplayComparison?
  let failures: [String: String]?
  let decision: ModelReplayDecision?
  let partial: Bool?
  let source: String?
  let createdAt: String?
}

struct ModelReplayComparison: Decodable {
  let a: ModelReplayAssessment?
  let b: ModelReplayAssessment?
  let disagreement: Bool?
  let disagreedFields: [String]?
  let chosenRoute: String?
  let confidence: Double?
  let writesCount: Bool?
  let writesPrice: Bool?
}

struct ModelReplayAssessments: Decodable {
  let fable: ModelReplayAssessment?
  let astraReplay: ModelReplayAssessment?
}

struct ModelReplayAssessment: Decodable {
  let model: String?
  let category: String?
  let condition: String?
  let confidence: Double?
  let recommendedAction: String?
  let rationale: String?
  let identityCandidates: [ModelReplayIdentity]?
}

struct ModelReplayIdentity: Decodable {
  let label: String
  let confidence: Double?
}

struct ModelReplayJev: Decodable {
  let model: String?
  let choice: String?
  let confidence: Double?
}

struct ModelReplayDecision: Decodable {
  let action: String
  let reason: String
  let disagreement: Bool?
}

struct ModelPipelineStatus: Decodable {
  let status: String
  let role: String
}

struct ModelPipelines: Decodable {
  let fable: ModelPipelineStatus?
  let astra: ModelPipelineStatus?
  let jev: ModelPipelineStatus?
  let astraLive: ModelPipelineStatus?
  let note: String?
}
