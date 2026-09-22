# Implementation checkpoints

**Last updated:** 2026-09-22
**Source of truth:** `IMPLEMENTATION.md` (entire project, 24-hour clock) and `FINAL-PLAN.md`

A stage is complete only when every item in that stage and its exit gate in `IMPLEMENTATION.md` are done. Nothing is deferred.

## Status legend

- [x] Complete and verified
- [ ] Not complete

## Clock

- [x] T+0–4h Stage 1
- [ ] T+4–8h Stage 2 (original fixture/storage gate passed; added physical row gate open)
- [ ] T+8–12h Stage 3 (original fixture gate passed; added physical row identity gate open)
- [ ] T+12–16h Stage 4
- [ ] T+16–24h Stage 5 (models, RL, review, security, eval, demo)



## Project setup

- [x] Read `IMPLEMENTATION.md` and `FINAL-PLAN.md`.
- [x] Verify env vars present without exposing values.
- [x] Local env file untracked.
- [x] Record providers: OpenAI voice + small models; Fable and Astra roles preserved.
- [x] Confirm OpenAI API key reuse.
- [x] Confirm OpenAI voice model ID (`gpt-4o-mini-tts`).
- [x] Repository skeleton created across backend, iOS, CV, schemas, evaluation, and fixtures.
- [x] iOS `LibrarySurvey` app and generated Xcode project are in the repo.
- [x] A dedicated Library Survey app icon is generated and wired into the iOS asset catalog.
- [x] `cv/library_vision` package exists for Stage 2 implementation.
- [x] Lint, test, backend-run, XcodeGen, and simulator-build commands documented and green.



## Schemas (full IR)

- [x] Survey geography schema.
- [x] Capture-package schema.
- [x] Full Survey IR schema with every entity family in `IMPLEMENTATION.md`.
- [x] Model-assessment schema.
- [x] Evidence-package schema.
- [x] RL-transition schema.
- [x] Price-observation schema.
- [x] Review-decision schema.
- [x] Fixtures validate for every schema.



## Stage 1 — T+0–4h — Package, location, room, 2D/3D

- [x] FastAPI create, upload, seal, get survey.
- [x] Create Survey + Device Check + consent screens compile in the iOS app.
- [x] Camera, mic, and location-when-in-use permission strings and requests are present.
- [x] GPS reverse-geocode implemented; `74de486b` / `eb3f30fa` sealed with `source: gps`. Location-deny/manual is `69d0a6d6` / `0cbeda56` (`source: manual`, no coordinates).
- [x] RoomPlan Pass A, multi-room `StructureBuilder`, raw/processed structure, sampled RGB/poses, and USDZ implemented. LiDAR captures `69d0a6d6`, `0cbeda56`, `74de486b`, and `eb3f30fa` sealed to `geometry`.
- [x] Spoken audio and written notes share the capture monotonic clock (`eb3f30fa`: two written notes + `audio/timing.json`).
- [x] Local SHA-256 seal and resumable per-file upload implemented. Complete upload+seal includes `eb3f30fa` with intact hashed SVG + server `derived/` output. Operator confirmed Wi-Fi interrupt then Resume; `uploaded_files` has unique paths (no double write).
- [x] Tagged 2D plan and interactive USDZ on the sealed screen; operator confirmed they remain after force-quit restores the last sealed package.
- [x] Backend state sequence Created → Capturing → Uploading → IngestValidation → Geometry is integration-tested and observed on sealed surveys.
- [x] Server geometry now writes under `derived/` and cannot overwrite hashed capture files; immutable-SVG/restart and upload-retry integration tests pass.
- [x] iOS reopen verifies every manifest file off the main UI thread; operator confirmed relaunch restores the last sealed survey.
- [x] New audio captures write `audio/timing.json` on the same monotonic clock as written notes (`eb3f30fa` inspected).
- [x] Gate: `docs/stage-1-device-validation.md` rows recorded in `SESSION-RUN.md`. Transcription/TTS remains Stage 3.



## Stage 2 — T+4–8h — Count

- [x] Replace temporary SQLite persistence with Redis for Survey IR, metadata, idempotency, state events, and jobs; remove SQLite runtime dependency.
- [x] Configure authenticated Redis from server-side env; durable keys have explicit versioning and no accidental TTL; restart/atomicity tests pass.
- [x] Persist sealed media in S3-compatible object storage, with hashes/reopen checks and no dependency on ephemeral host disk.
- [x] Verify migration or explicit preservation of any existing local demo surveys before removing SQLite data.
- [x] Shelf map: unit, face A/B, rows.
- [x] Live blur, glare, speed, text-size, occlusion guidance.
- [x] Coverage heatmap and targeted recapture.
- [x] Spine instance detection, tracking, cross-pass association.
- [x] ISBN never used as merge key; double-sided faces stay separate.
- [x] `possibly_moved`.
- [x] ShelfFaceDataSize on IR, inventory, 2D.
- [x] On-device live assist.
- [x] Idempotent Vision jobs.
- [x] Gate: Redis/object-storage create/upload/seal/reopen and restart recovery pass; no SQLite dependency; reverse rescan does not double; uncovered rows partial.
- [ ] Added phone row gate: one for example 8–10 book row has a distinct outlined `AssetCopy` candidate for every visible spine, reverse sweep does not double, and detected/actual plus partial/recapture status are recorded.



## Stage 3 — T+8–12h — Identity, non-books, damage

- [x] Pass C on-device exception queue and backend review queue: unread spines, barcode/title page, damage, high-value, unbound notes.
- [x] On-device Vision barcode/OCR and provisional image outlines; ISBN-10/13 checksums, 979, ISSN, set/volume, separately typed library barcode.
- [x] Cache → Open Library → Google Books → manual identity chain; normalized Google output discards `saleInfo`.
- [x] Full closed taxonomy, including books, portraits/photo frames, mug/cup, electronics, furniture.
- [x] Mug counted with `valuation_required=false`; art/portrait `requires_appraisal`; excluded state explicit.
- [x] OpenAI timed STT and operator TTS; capture clock runs through Passes A–C; tap/reticle/focus time/pose/semantic association; unbound notes visible.
- [x] Damage assertion, close-up, and scale workflow; tap a provisional book/object outline to capture a zoomed crop while audio continues.
- [x] Review actions: bind note, rescan barcode, keep unresolved.
- [x] Gate exercised in `backend/tests/test_stage3_gate.py` and recorded in `SESSION-RUN.md`: invalid ISBN not eligible for ISBN price query; no-ISBN physical copy ID survives API restart; spoken portrait damage binds to its asset and close-up; mug is counted/excluded; unresolved queue visible; taxonomy covers every demo class.
- [ ] Added phone row identity gate: every copy in the Stage 2 row has a supported ISBN/name identity or a visible per-copy barcode/title-page/manual task; unread and no-ISBN examples exercised; distinct copies remain distinct.



## Stage 4 — T+12–16h — Web search + building

- [x] OpenAI Responses `web_search`, ISBN then name; market/`user_location` from geography.
- [x] Unique unpriced objects batched (5 per Responses call); at most 5 listing URLs per item; stop after a parsed amount.
- [x] Redis `found_prices` plus search attempt cap.
- [x] Price Evidence UI: listing URL, citations, confirm, manual.
- [x] Queue search per edition+market.
- [x] Same-row per-copy price status: validated ISBN then name fallback, shared-edition evidence without copy merge, reviewed local physical-book range or explicit pending/no-comparable reason for every eligible copy.
- [x] Filter eBook/rental/bundle; valuation range.
- [x] Building reconstruction from `demo_rebuild_rates_v1`.
- [x] Overview totals + spend ledger.
- [x] All price APIs.
- [x] Gate: ISBN and name-only web-search evidence; local market; mug still excluded.
- [ ] Added row gate: verify price/status roster against every eligible physical copy in the Stage 2/3 row; no draft result presented as a confirmed price.



## Stage 5 — T+16–24h — Models, RL, product, eval, demo



### Models and Jev

- [x] Evidence package frozen; identical input to A and B.
- [x] Fable batch adapter.
- [x] Astra replay adapter.
- [x] Astra-live assist.
- [x] Shared assessment schema wired end-to-end.
- [x] Jev routing: accept, recapture, human, alternate resolver.
- [x] Policy vetoes (high-value, eBook-as-physical, ISBN-only merge).
- [x] Cost, latency, disagreement logs.



### RL home

- [ ] `RLTransition` on every decision, have RL feedback layer to improve on things.
- [x] Replay buffer.
- [x] Offline bandit trainer exercised on a synthetic label fixture; physical labeled set remains in Eval and demo.
- [ ] Recapture sequential transitions.
- [x] Specialist heads: condition, eligibility, damage, duplicate-features, quality.
- [ ] Policy registry, shadow, rollback by `policy_id`.
- [x] Reward from independent labels (§13 table).



### Product, security, failures

- [x] Remaining screens: Processing, Overview, Inventory, Review, Report.
- [x] JSON + PDF report with versions, citations, limitations.
- [ ] Evidence viewer for every count and value.
- [ ] Shelf-row inventory detail with expected/detected count, selectable copy outlines and evidence, identity/unresolved action, and per-copy price/condition status; barcode rescan, correction, and search actions.
- [ ] Auth, encryption, signed URLs or local equivalent, retention, redaction, access log.
- [ ] Accessibility requirements.
- [x] Spend ledger with no stop-at-cap.
- [ ] Every §17 failure row as status + operator action.
- [x] Remaining APIs: jobs, inventory, review, report, shelves, evidence, policies, shadow.



### Eval and demo

- [ ] Labeled zone with every §23 case.
- [ ] Metrics with numerator/denominator.
- [ ] Holdout without retuning.
- [ ] Demo script 1–13 run and recorded.
- [ ] Added physical-device 8–10 book row demo: manual roster reconciled to distinct copy records, identity or Pass C tasks, price status, inventory/report, and numerator/denominator recorded in `SESSION-RUN.md`.
- [ ] Gate: full product; RL home; spend and limitations disclosed; ask-map fully landed.



## Completion policy

A checked item must have code or an artifact. A stage is complete only after its `IMPLEMENTATION.md` gate is exercised and recorded in `SESSION-RUN.md`.
