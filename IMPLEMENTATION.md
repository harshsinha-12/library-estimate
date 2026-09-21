# Implementation — entire project, A to Z, 24 hours

**This file is the build.** Architecture stays in `FINAL-PLAN.md`. Checkboxes live in `CHECKPOINTS.md`. Session log is `SESSION-RUN.md`.

**Clock:** 24 hours of elapsed work, stages in order. A few hours over is acceptable. The clock is not permission to drop scope. **Nothing from this project is skipped, deferred, or marked “later.”**

**Already started:** JSON schemas, FastAPI survey create/upload/seal, domain models, Fable/Astra contract stubs. Continue from there; finish every item below.

```text
T+0h   Stage 1  Package, location, RoomPlan, 2D/3D
T+4h   Stage 2  Shelf count, coverage, data size, live assist
T+8h   Stage 3  ISBN, catalog, non-books, speech, damage
T+12h  Stage 4  OpenAI web-search prices, building reconstruction, overview
T+16h  Stage 5  Fable, Astra live+replay, Jev, RL, report, eval, demo
T+24h  Done    Full survey → report, every requirement landed
```

Order of work inside the 24 hours (do not invert):

> Capture and count first. Identity second. Price third. Models, RL, report, and eval last. All of them still ship in this clock.

## Frozen demo defaults

| Question | Default |
| --- | --- |
| Device | LiDAR iPhone or iPad |
| Scope | One controlled 50–100 book zone; IR still supports multiple rooms |
| Pull books for barcode | Yes, Pass C |
| Book valuation | Replacement range from comparable physical offers |
| Geography | Core Location When In Use → country/city → search market; manual override |
| Price discovery | OpenAI Responses `web_search` only. Unique unpriced objects go in batches of 5. ISBN first, else name. At most 5 listing URLs per item. Drafts need human confirm. No Bing scrape and no dedicated Amazon product-page scraper |
| Small models | OpenAI `gpt-5.6-luna` for query build and listing-excerpt parse |
| Building value | Floor area × `demo_rebuild_rates_v1` |
| Backend | Python FastAPI + Redis for Survey IR, metadata, state, idempotency, jobs, and cache; S3-compatible object storage for sealed media. No SQLite in the target backend. |
| App | SwiftUI |
| Voice | OpenAI TTS (exact model ID in `.env.local`) |
| Pipeline A | Fable / Anthropic |
| Pipeline B | Astra replay on the sealed package |
| Live assist | On-device Vision plus optional sampled Astra-live; never stored as Pipeline B |
| Jev | Typed router over A, B, and deterministic flags |
| RL | Full home: transitions, replay buffer, offline trainer, specialist heads, policy registry, shadow. No per-survey live weight update |

OpenAI Responses `web_search` (batched unique unpriced items, market from survey geography):

```text
POST https://api.openai.com/v1/responses
tool = web_search
user_location = { country, city, region }
text.format = json_schema (one item or a batch of up to 5)
```

Unique unpriced objects share one Responses call, up to five at a time. Once a physical amount is stored in Redis `found_prices`, that object is not searched again (at most five attempts if still unpriced). Show the listing URL and citation URLs in the Price Evidence screen. Live Shelf Pass B OCR reads whatever cover or spine is in view and searches by that name; after seal, shelf frames are read the same way before the price queue.

## Complete product (nothing omitted)

If it is listed here, it is in the 24-hour build.

### Capture app (all 15 screens)

1. Create Survey — consent, valuation basis, When In Use location, reverse-geocode, override country/city/currency/search market  
2. Device Check — LiDAR, storage, battery, camera, mic, location, network optional  
3. Room Pass A — RoomPlan, name rooms, joins, incomplete-scan warnings  
4. Shelf Map — shelf units, face A/B  
5. Shelf Pass B — quality overlay, coverage heatmap, recapture strips, live assist  
6. Other Assets — portraits, electronics, furniture, excluded objects  
7. Speak / Write Notes — tap-first; unbound notes visible; OpenAI STT/TTS as configured  
8. Exception Pass C — barcode, title page, damage close-up, high-value queue  
9. Seal and Upload — hashes, pause/resume, local copy until ACK  
10. Processing — per-stage status and actionable failures  
11. Overview — area, coverage, copy count, shelf data size, editions, contents range, building reconstruction, city/market, unresolved  
12. 2D / 3D — RoomPlan USDZ + SVG; tap shelf/asset; occupied metres and copy count on shelves; evidence links  
13. Inventory — copies vs editions; filter; Search prices one book or queue all  
14. Review — merge/keep, edition, barcode, bind note, confirm price, appraisal  
15. Report — signed-off JSON + PDF, manifest, rate-table version, `policy_id`, limitations, spend  

Accessibility: Dynamic Type, VoiceOver, high-contrast non-color status, large targets, captions/transcripts, one-handed capture.

Permissions: camera, microphone, location when-in-use. Location is one reading at create-survey, not continuous tracking. Precise lat/lon only with extra consent.

### Capture package

`survey_<id>/` with `manifest.json`, `property.json`, `device/location.json`, `device/calibration.json`, RoomPlan raw/processed/structure/USDZ, shelf videos/frames/poses/quality, closeups, audio + segments, notes, `checksums.sha256`. Resume after interrupt. Backend rejects path traversal, hash mismatch, bad schema, bad timestamps.

### Survey IR (full, not a stub)

Observation, Track, AssetCopy, BookEdition, Work, Identifier, PriceObservation, Valuation, ShelfFaceDataSize, BuildingValuation, SurveyGeography, ModelAssessment, PipelineRun, ReviewDecision, RLTransition. ISBN is never the AssetCopy primary key. Every material field has value, unit, status, confidence, interval, method, evidence_refs, run_id.

### Computer vision

Quality components (not a fake single score). Coverage in shelf-face coordinates. Hierarchy: room → shelf unit → face → level → spine → physical copy. Vertical, leaning, thin, occluded, stacks, double-sided, glass/glare. Within-sweep tracking. Cross-pass spatial association. Hard rules: ISBN alone does not merge; different faces are different copies; identifier contradiction blocks auto-merge; `possibly_moved` when identity matches and space jumps.

### Identity

Evidence ladder: rear-cover EAN → printed ISBN → title/author/publisher/edition catalog match → unknown copy. Check digits, ISBN-10/13, 979, ISSN, boxed sets, library barcodes. Open Library + Google Books as identity only. Google Books `saleInfo` never prices a physical copy.

### Non-books, speech, damage

Taxonomy: book, serial, painting, portrait, sculpture, computer, monitor, printer, furniture, shelf, appliance, cup, decorative_object, other. Mug inventoried with `valuation_required=false`. Damage type/severity/region/evidence. Spoken note is an assertion, not truth. Associate by time, reticle, tap, pose, semantics; otherwise unbound.

### Pricing and building

OpenAI Responses `web_search`: ISBN first, else name. Unique unpriced items in batches of 5. Market from location (`en-IN`, `it-IT`, `ja-JP`). Filter eBook/rental/bundle. Range + timestamp + citation. Building: `floor_area × demo_rebuild_rates_v1[country]`, basis `replacement_cost`, not sale price. Contents and building never mixed.

### Models

Live assist during capture (on-device; optional sampled Astra-live). After seal, **same** evidence package to Fable (A) and Astra replay (B). Jev + policy: accept, recapture, alternate resolver, human. Models do not write geometry, checksums, currency math, or prices. Policy vetoes: high-value auto-final, eBook-as-physical, ISBN-only merge, both-models-wrong-vs-deterministic.

### RL (full home)

`RLTransition` on every decision. MDP: episode = survey; actions = accept / recapture / human / specialist / frontier. Reward table from `FINAL-PLAN.md` §13. Replay buffer, offline trainer, specialist heads (condition, eligibility, damage, duplicate-features, quality), policy registry, `GET /v1/policies`, `POST /v1/policies/{id}/shadow`. Stage 0 log is implemented **and** Stages 1–5 of the RL path run on the labeled demo set (bandit fit, specialist fit, shadow). Production weights still do not update from a single live survey.

### Backend API (all routes)

```text
POST   /v1/surveys
GET    /v1/surveys/{id}
POST   /v1/surveys/{id}/uploads
POST   /v1/surveys/{id}/seal
GET    /v1/surveys/{id}/jobs
GET    /v1/surveys/{id}/inventory
GET    /v1/surveys/{id}/review
POST   /v1/reviews/{id}/decision
POST   /v1/assets/{id}/price-search
POST   /v1/surveys/{id}/live-price-search
POST   /v1/surveys/{id}/identify-and-price
POST   /v1/surveys/{id}/price-search-queue
POST   /v1/assets/{id}/price-observations
GET    /v1/surveys/{id}/report
GET    /v1/surveys/{id}/shelves
GET    /v1/evidence/{id}
GET    /v1/policies
POST   /v1/policies/{id}/shadow
```

Idempotency keys. Job keys `survey_id + stage + input_hash + pipeline_version`. State machine from `FINAL-PLAN.md` §15, including Partial and RecaptureRequired. Every failure row in §17 is implemented as a status + operator action, not as a crash.

### Security

Encrypt local packages and transport. Signed short-lived upload URLs (or local equivalent with the same contract). Secrets only in `.env.local`. Consent UI. Face/bystander redaction hook. Retention/deletion. Immutable original hashes. Version schemas, models, prompts, policies, rate table. Append-only human decisions. Per-survey $50 ledger.

### Evaluation

Labeled zone: two same-ISBN copies, reverse rescan, no ISBN, ambiguous edition, moved book, damaged book, portrait + spoken damage, mug, appraisal item. Metrics: coverage, count, dedup, identity, OCR/barcode, damage, geometry, pricing, models, product. Release gates in `FINAL-PLAN.md` §18 all exercised. Numerator/denominator on every metric. Spend recorded.

---

## Stage 1 — T+0h to T+4h — Package, location, room, 2D/3D

**Goal:** technician creates a survey, location works, one room is scanned, package seals, 2D and 3D show.

### Work

- Finish repo: `ios/LibrarySurvey`, `backend/app/{api,domain,workflows,providers,policy,rl}`, `cv/library_vision`, `schemas`, `eval`, `fixtures`
- Schemas: Survey IR (full entities), capture package, geography, evidence package, model assessment, RL transition, price observation, review decision
- Fixtures that validate
- SwiftUI app; Info.plist camera, mic, location when-in-use
- Create Survey + Device Check + consent
- Location → reverse-geocode → `SurveyGeography`; deny path is mandatory manual country/city
- RoomPlan Pass A on shared AR session; sampled RGB + poses; fallback sequential room/shelf modes with one frame if dual-camera fails
- Audio + written notes on one monotonic clock
- Seal manifest + SHA-256; survive interrupt; resumable upload
- FastAPI create / upload / seal / get; idempotency; package validation
- Geometry worker: raw + processed RoomPlan, `CapturedStructure` when multiple rooms exist
- 2D tagged floor plan (numbered colored walls, centimetre lengths, door/window gaps, compass N = scan +Z, not magnetic north) plus shareable SVG; 3D USDZ view on the sealed/upload screen. Visual reference: `docs/cosmo-tagged-plan-reference.png`
- Reuse Cosmo RoomPlan export/hashing/geometry code only as reference; new names, new schema. Shelf footprints and asset pins stay Stage 2 overlays on this same plan

### Gate

Location deny still works. Sealed package reopens with matching hashes. 2D and 3D visible. `device/location.json` is `gps`, `manual`, or `mixed`. Survey state machine at least Created → Capturing → Uploading → IngestValidation → Geometry.

---

## Stage 2 — T+4h to T+8h — Count every visible copy

**Goal:** Pass B counts physical books, reports coverage and data size, does not double-count a reverse rescan.

### Work

- Replace the temporary Stage 1 SQLite repository with a Redis repository before adding new inventory state; remove SQLite runtime dependency and update tests.
- Use the supplied Redis host, port, username, and password via server-side configuration; fail startup clearly if Redis is unavailable. Do not expose credentials to the iOS app.
- Persist survey records/IR, uploaded-file metadata, idempotency records, state events, Vision jobs, and future review/RL state in namespaced Redis keys with explicit serialization/versioning and no accidental TTL on durable records. Use atomic operations for state transitions and idempotency.
- Move accepted evidence bytes from the development filesystem to S3-compatible object storage; retain SHA-256 verification and immutable package references in Redis. Do not put large RGB/audio/USDZ blobs in Redis or rely on ephemeral host disk for deployment.
- Test Redis-backed create/upload/seal/reopen and restart recovery before migrating or discarding any existing local demo data.
- Shelf Map + Shelf Pass UI: unit, face A/B, rows
- Live quality: blur, glare, speed, text pixel height, occlusion → operator messages
- Coverage heatmap; recapture named rows only
- CV: shelf → face → level → spine instance → temporal track
- Cross-pass association by room/shelf/face/row/3D; ISBN is not a merge key
- Double-sided shelves: face normal prevents walkaround merge
- `possibly_moved` flag
- `ShelfFaceDataSize`: occupied length, capacity, fill, item count, unresolved, evidence bytes
- On-device live assist (provisional count, recapture hints)
- Persist observations, tracks, AssetCopy candidates
- Jobs: Vision stage, retries, no duplicate assets on rerun

### Gate

Redis-backed create/upload/seal/reopen and restart recovery pass with no SQLite dependency. Labeled shelf: count interval disclosed; reverse rescan does not double; two copies in different slots stay two rows; uncovered rows are `partial`, never silent zero. Data size on inventory and 2D overlay.

---

## Stage 3 — T+8h to T+12h — Identity, everything else, damage

**Goal:** know which books they are, count non-books, bind “this portrait is damaged.”

### Work

- Pass C exception queue: unread spines, barcode, title page, damage, high-value, unbound notes
- Vision barcode + OCR; ISBN-10/13 checksum; 979; ISSN; set vs volume; library barcode typed separately
- Identifier ladder and catalog chain: cache → Open Library → Google Books → manual
- Closed taxonomy including mug/portrait/electronics/furniture
- Policy: `valuation_required`, `requires_appraisal`, excluded
- Speech-to-text (OpenAI) on the capture clock; TTS for operator prompts
- Note association: time, reticle, tap, pose, semantics; else unbound
- Damage observation + close-up + scale
- Other-assets marking during/after Pass B
- Review actions: bind note, rescan barcode, keep unresolved

### Gate

Bad checksum never prices. No-ISBN copy is stable. Portrait spoken damage links to the correct asset and close-up. Mug counted and excluded. Unresolved queue is visible. Taxonomy covers every demo object.

---

## Stage 4 — T+12h to T+16h — Web-search prices and the building

**Goal:** local contents range and a labeled reconstruction number.

### First: one complete shelf row, from capture to price status

Before the general pricing work, exercise the completed Stage 2–3 capabilities on one physical-device row of 8–10 books. Show a provisional outline for each visible spine, reconcile one stable `AssetCopy` per physical book with a manual row roster, and verify a reverse sweep does not duplicate copies. If any slot is missed or unreadable, retain an explicit count interval/partial state and named recapture action. Give every copy a validated ISBN/catalog or supported name identity, or a visible per-copy Pass C barcode/title-page/manual task. Preserve distinct copies even when they share an edition. A distant sweep cannot establish every title or barcode. Carry each eligible copy through price search to a reviewed local physical-book range or an explicit pending/no-comparable state. Show the whole row and each copy's evidence and status in a usable inventory row detail. This is Stage 4 work; the recorded Stage 2 and 3 gates remain complete.

### Work

- Build the row detail first: expected/detected count, coverage, selectable spine outline and evidence per copy, title/ISBN source or unresolved task, condition, search state, reviewed range or pending reason, and correction/barcode-rescan/per-book search actions.
- Reconcile the 8–10 book row on the phone; retain each physical-copy ID and row/slot, record detected/actual, and expose missed slots and reverse-sweep duplicates for correction before pricing.
- For every row copy, link identity evidence or a visible unresolved Pass C action; never infer an ISBN from a title or silently omit an unread book.
- Query builder (templates): ISBN first, else name+author+publisher+edition+country
- OpenAI Responses `web_search` with survey geography as `user_location`; unique unpriced items in batches of 5; Redis `found_prices` stops further searches for that object
- Parse citations to draft `PriceObservation`s; filter eBook/rental/bundle/wrong format
- Price Evidence UI: query string, listing URL, citations, drafts, confirm, manual reason
- Queue search for every found edition+market
- For the 8–10 book row, expose a price-search/status entry for every eligible physical copy. Shared edition+market searches may reuse evidence, but each copy retains its own condition and valuation status. Search by validated ISBN, otherwise by recognized name; unresolved identities remain `price_pending`/unpriced with an action, never zero-valued or silently omitted.
- Valuation range, FX snapshot, freshness, status `estimated|quoted|manual|requires_appraisal|unavailable`
- Building: area × `demo_rebuild_rates_v1` for IN/IT/JP; label basis and table version
- Overview totals: contents range, building, unresolved, city/market
- Spend ledger line for search
- All price APIs listed above

### Gate

ISBN book and name-only book both have web-search evidence. IT/JP/IN queries are not US. Mug still excluded. Building number shows rates + area. Ledger records search cost.

**First Stage 4 gate:** on a physical device, follow one manually labeled 8–10 book row from live outlines to inventory. Record detected/actual; confirm one stable record per visible book and no reverse-sweep duplicate; show an evidenced ISBN/name or explicit unresolved task for each copy. Every eligible resolved copy has a cited, reviewed local physical-book range or a visible pending/no-comparable reason. Verify name fallback and shared-edition evidence without collapsing copies; drafts are not confirmed prices. Record per-copy outcomes and priced/eligible numerator/denominator in `SESSION-RUN.md` before completing Stage 4.

---

## Stage 5 — T+16h to T+24h — Models, Jev, RL, review, security, eval, demo

**Goal:** the rest of the system, including evaluation and the demo script. This stage is large on purpose; it is still in the 24-hour clock.

### Work — models and Jev

- Freeze evidence-package schema; same bytes to A and B
- Pipeline A Fable adapter (Anthropic key)
- Pipeline B Astra replay adapter (independent; no Fable output in the prompt)
- Optional Astra-live sampled during capture; logged as assist, not as B
- Shared `ModelAssetAssessment` schema
- Jev typed decisions + probabilities
- Policy engine: value/risk thresholds, retry budget, veto list
- Disagreement, calibration, cost, latency logged per run

### Work — RL

- Write `RLTransition` for every accept/recapture/human/specialist/frontier action
- Replay buffer (append-only)
- Offline contextual-bandit fit on the labeled set
- Sequential recapture transitions
- Train specialist heads on demo labels: condition, eligibility, damage, duplicate-features, quality
- Policy registry with `policy_id`, hashes, stage, approved_at
- Shadow endpoint; pin/rollback by `policy_id`
- Reward computed from independent labels using the §13 table

### Work — product finish

- Review queue UI and `POST /v1/reviews/{id}/decision`
- Evidence viewer for every count and value
- JSON + PDF report: property, geometry, inventory, damage, contents, building, unresolved, methodology, versions, listing citations, policy_id, limitations
- Auth, encryption, signed URLs or local equivalent, retention/deletion, access log, redaction control
- Accessibility pass on capture and review
- Per-survey $50 ledger with stop-at-cap
- Failure table §17 all wired

### Work — evaluation and demo

- Ground-truth protocol for the 50–100 book zone (every case in `FINAL-PLAN.md` §23)
- Metrics with numerator/denominator
- Holdout pass without retuning
- Demo script, in order:
  1. RoomPlan + location-derived market  
  2. Coverage warning + recapture  
  3. Physical count before identity  
  4. Reverse scan does not double  
  5. Same-ISBN copies stay two  
  6. ISBN validation + catalog evidence  
  7. Search prices ISBN then name; confirm price  
  8. Portrait audio → damage close-up  
  9. Mug excluded; art → appraisal  
  10. A/B disagreement, Jev, policy, human  
  11. 2D, 3D, inventory, ranges, data size, unresolved, audit report  
  12. Metrics, spend, failures, limitations  

### Gate

Every item in “Complete product” above exists in the app or backend. The demo script runs end to end. RL transitions exist; specialist heads and a shadow policy exist; live weights do not change from one survey. Spend and limitations are disclosed. No requirement from `question.md` is missing from the landing map in `FINAL-PLAN.md` §3.

---

## Mapping

| Clock | This file | `FINAL-PLAN.md` |
| --- | --- | --- |
| T+0–4h | Stage 1 | Phases 0–1, §§4–6, 10, 16.1–3, 22 (package) |
| T+4–8h | Stage 2 | Phase 2, §7, shelf data size |
| T+8–12h | Stage 3 | Phases 3–4, §§8–9 |
| T+12–16h | Stage 4 | Phase 5, §11, building rates |
| T+16–24h | Stage 5 | Phases 6–8, §§12–13, 15–18, 21–23 |

## How to use this file

Work the five stages in order on the 24-hour clock. Check off the matching lines in `CHECKPOINTS.md`. When a gate fails, fix it in that stage; do not jump ahead and do not delete the failing requirement. The project is complete only when Stage 5’s gate is true.
