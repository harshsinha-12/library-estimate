# Leftover work

**Date:** 2026-09-22  
**Sources:** `question.md`, `IMPLEMENTATION.md`, `FINAL-PLAN.md`, `CHECKPOINTS.md`, `SESSION-RUN.md`, and the current iOS/backend/CV code. Read question.md and maybe other files if doubt again.

Harsh will most likely be sleeping, or lying on the bed with the laptop open, and working through remote access in Cursor or Codex via his phone. Keep this plan. Do not wait for a rewrite of the leftover list.

Price discovery stays on the OpenAI Responses API `web_search` tool already in this repo (`user_location` from survey geography, ISBN then name, batches of unique unpriced objects). That path is fine. Do not add the Bing Search API. Do not scrape Amazon.

Do not run the physical device, the library scan, or any other step that needs Harsh present until he is back. Coding, unit tests, fixture gates, and other checks that do not need his permission should proceed. If a step needs his permission, the phone, a live survey on device, or a decision only he can make, skip it and leave it for when he returns.

This is the remaining build. Fixture tests and adapters do not close an item. Invertis Library (45 copies) is the device book pass; there is no separate 8–10 row gate.

After finishing the task, mention that you have done this so that, if I can see that on my phone, I know that this has been completed, and I will tell you to move to the next one or point You to the next one.

---

## Already not leftover

- Stage 1 capture package, RoomPlan 2D/3D, GPS or manual market, seal, Redis, R2. - Done manually
- Stage 2–4 **fixture** gates: reverse rescan on labeled JSON, ISBN-not-a-merge-key, mug excluded, portrait damage binding, web-search drafts, building reconstruction, $50 ledger. - Done manually but make sure books should not recount or be counted twice unless the cases as stated in the question.md
- JSON/PDF report adapters, Fable/Astra/Jev **clients**, offline bandit trainer on synthetic labels.

---

## 1. Camera: RoomPlan owns the session

Apple will not let RoomPlan and a second `AVCaptureSession` / optical zoom share the camera.

Today:

- Pass A: `RoomCaptureView` owns the camera. RGB is copied off that `ARSession`.
- Pass B: a new `ARView` after RoomPlan is torn down. Shelf poses are not in the RoomPlan coordinate frame. Shelf pins on the 2D plan are placeholders, not measured footprints.
- “Zoom” is a JPEG crop, or a separate `UIImagePickerController` on Pass C.

Left:

1. Never start `UIImagePicker` / AVFoundation while RoomPlan or the shelf `ARView` is running. Pass C camera only after that session is stopped.
2. Do not promise optical zoom during Pass A. Close-ups are a later still, after the geometry session is released.
3. Either share RoomPlan’s `ARSession` into Pass B, or keep sequential modes and register shelf faces onto the finished plan (operator-placed footprints are enough for the demo). The two spaces are currently disconnected.
4. If dual-camera fails, sequential room-then-shelf with one RGB frame is the documented fallback. That failure is not a productized status in the UI.

---

## 2. Multi-book count

`question.md` requires: every visible copy, spines on both faces, reverse sweep does not double, same title in another bay still counts as a second copy.

That rule is proven on labeled JSON and on the Invertis Library phone scan (45 copies). Live detection is `VNDetectRectanglesRequest` (tall + thin + three letters). Association is shelf-face coordinates. Unread rectangles are not minted. Live ISBN is never written.

### Detection structure

Scoped to the 50–100 book demo. The 200,000-spine production problem is not this clock; the demo still needs a real shelf-to-spine structure, not a generic object classifier.

5. Build the hierarchy at capture: room → unit → face A/B → row → spine instance → physical copy. Stop binning spines by the Y of the current JPEG.
6. Persist an outlined candidate for every visible spine on that row, not only rectangles in the last frame.
7. Associate across frames in shelf-face coordinates. Reverse sweep updates evidence; it does not mint a new copy. Face B is a different copy. Same ISBN in two slots stays two IDs.
8. Handle leaning, thin, stacked, occluded, and glare. If two spines cannot be separated, the row stays `partial` with a count interval — never a silent undercount.
9. Stop the fake heatmap. Coverage of a named row only increases when that row was actually in view and readable.

### Resolution when a spine is unreadable

“Move closer” is not the whole plan.

10. Named recapture strip for the missed row/slot.
11. Pass C: pull the book, rear-cover barcode, or title/copyright page, bound to that slot.
12. Manual name/ISBN typed onto that copy, still a separate `AssetCopy`.
13. Unresolved is a first-class status. No inferred ISBN from a title.

### Edition

14. Paperback vs hardcover vs library binding as format, not as a merge key.
15. Set ISBN vs volume ISBN as two identifier kinds. The Pass C UI has a `scope` field; `identifiers.py` does not.
16. Two same-title different-edition copies stay two editions. Catalog match cannot invent the ISBN.

### Cost at scale

Honest for $50 and 50–100 books. Not a 200k-spine production claim.

18. Detect and track on device during Pass B. After seal, YOLO 11x-seg from bookshelf-scanner is the initial count and crop source when it returns books — not only when it finds more spines than Vision. Those crops go to Fable/Astra and to title identity (`docs/architecture.md` §5.7).
19. Price search once per unique edition + market; reuse evidence per copy.
20. Do not skip Astra/Fable on most books to save money (see section 3). For this demo, run A and B on every copy in the sealed zone. At 200k, unique-edition search plus on-device count is the only affordable split; say that on the report instead of hiding it.

---

## 3. Fable, Astra, Jev

Today the operator taps **Run Fable, Astra, and Jev** on one inventory row. Most copies never see a model. Jev reads A and B and proposes accept / recapture / human. Policy then vetoes. That is an aggregator on a subset.

What the assignment asked:

- Astra during capture, end to end on the live pass (quality, provisional count, unreadable slots). Log it as assist. It is not the sealed score.
- After seal, Pipeline A (Fable) and Pipeline B (Astra replay) on the **same** package, independently, for **every** copy in the demo zone — not only router-selected hard cases.
- Jev does not merge answers into inventory truth. Jev scores both outputs (agree/disagree, calibration, which one matched gold). That score is the RL signal. Checksums, geometry, currency, and fetched prices stay in code.

Left:

21. Astra-live on Pass B/C, sampled so it fits the $50 cap, stored as assist metadata only.
22. After seal, automatic A and B on every `AssetCopy` in the demo survey (same evidence bytes, neither sees the other). Button-only replay is not the pipeline.
23. Jev emits a comparison record: A fields, B fields, disagreement, chosen route, confidence — and a separate policy layer may still veto. Jev must not write the count or the price.
24. Failed or unavailable provider → disclosed partial, human review, no invented assessment.
25. Live model IDs for the Invertis run are in `logs/llm_calls.json` and `docs/invertis-library/llm-trace-excerpts.json`: Pipeline A `claude-fable-5.1` (Fable role), Astra Extra / Astra-live `gpt-6-astra`, Jev `jev-latest` (ledger `jev-1.13.0`). Spend is on the survey ledger (Invertis ~USD 2.74). Repo default is `FABLE_MODEL=claude-fable-5-1` if not overridden.
26. Models still must not write geometry, ISBN, merge, or money.

---

## 4. RL while mentioning the model/technicque used

`backend/app/rl/offline.py` has a reward table (false merge −5, missed high-value −8, mug exclusion +0.2, and the rest of the §13 weights). Live transitions keep `reward: null` until independent labels arrive. Model auto-accepts now have a separate evidence-hash/A/B/Jev/policy audit, and recapture decisions reserve a `next_state_id` that the successor-state API can bind once to verified new evidence. Invertis logged **96** policy transitions and **0** independent labels. No gold freeze (`freeze_gold_set` requires 50–100 copies; Invertis has 45). Live `route_v0_log_only` does not set `logging_propensity`, so those rows cannot enter the bandit fit. `approved_at` remains null. Specialist heads have only synthetic exercise.

Left:

27. Done in code: `RLTransition` for model routing, Stage 3 bind/keep/rescan, identity correction, and Stage 4 price confirm/manual/no-comparable. Invertis exercised the model-routing path only (no price confirms on that survey).
28. Done in code: §13 reward from independent labels, never from “Jev agreed with Fable.” No physical labels posted yet.
29. Done in code: auto-accept audit. Invertis had 0 auto-accepts (policy sent every copy to `human_review`).
30. Gold set: the labeled 50–100 book zone with every demo case (two same-ISBN copies, reverse scan, no ISBN, ambiguous edition, moved book, damaged book, portrait + spoken damage, mug, appraisal item). Freeze it before any training. Invertis cannot freeze (45 copies).
31. Classification credit: condition / eligibility / damage / keep-vs-merge heads are scored against gold, and that score is part of reward. A router-only bandit is not “your own models.”
32. Finish sequential recapture on the phone: action → reserved `next_state_id` → new verified evidence. The backend successor-state path exists; automatic capture binding and a physical run remain open.
33. Promotion gate: train on survey IDs disjoint from holdout; shadow; report reward and calibration with numerator/denominator; **approve** a `policy_id`; pin the previous id to roll back. Shadow-only with `approved_at: None` is not promotion.
34. One live survey must not change production weights. That constraint is already true; keep it.

---

## 5. Inventory, evidence, and operator product

Needed to see sections 1–4, not as a separate product track.

35. Row detail: expected/detected, coverage, selectable outline per copy, identity or Pass C task, condition, search state, reviewed range or pending reason, barcode rescan, correction, per-book search.
36. Evidence viewer for every count and value (crop, listing URL, citation, geometry), not only “this path is a JPEG.”
37. Portrait spoken damage and mug exclusion must be visible on a real survey, not only `test_stage3_gate.py`. Other-assets and notes may stay folded into Room / Pass C.
38. Processing maps each `FINAL-PLAN.md` §17 failure to a status plus next action (blur, checksum, eBook-only, location mixed, provider down, both models wrong vs deterministic, upload interrupt). A raw state timeline is not that table.
39. 2D/3D after seal: tap shelf/asset → that copy’s evidence. Requires item 3 (registration) or an explicit “unregistered overlay” label.

---

## 6. Security, accessibility, eval, demo

Code/readiness pass completed 2026-09-22: the repo now has a Caddy HTTPS deployment artifact; iOS Data Protection and backup exclusion for sealed packages; a configurable, fail-closed Vision face-redaction hook with a package audit; reviewed-plan-only R2 encryption migration and retention execution; Dynamic Type-safe layouts, scaled targets, non-color status, VoiceOver semantics/announcements, and visible TTS transcripts with AI disclosure; strict holdout preflight/evaluation provenance; and one canonical 12-step runbook. These artifacts do not prove a private deployment or physical-device result.

40. Operate HTTPS in front of the API before an operator token is used. On the private environment, enable and verify phone redaction, migrate existing R2 plaintext or intentionally leave it in an isolated non-encrypted namespace, and execute retention only after reviewing the exact dry-run plan ID.
41. Run the implemented accessibility flow on-device with VoiceOver and the largest Dynamic Type sizes; verify focus/announcement order, contrast, camera/AR fallback descriptions, one-handed controls, and TTS transcript/audio behavior.
42. Capture a holdout survey never used for training, freeze an independent 50–100-copy roster, pass `eval.preflight`, and archive real numerator/denominator metrics from `scripts/evaluate_stage5.py`. Templates and fixture results are not accuracy.
43. Demo script, in order, on a LiDAR device, recorded:

    1. RoomPlan + location market
    2. Coverage warning + named recapture
    3. Count before identity
    4. Reverse scan does not double
    5. Same-ISBN copies stay two
    6. ISBN/catalog evidence
    7. Search ISBN then name; confirm physical price
    8. Portrait audio → damage close-up
    9. Mug excluded; art → appraisal
    10. A and B both ran; Jev scored them; policy; human
    11. 2D, 3D, inventory, ranges, data size, unresolved, report
    12. Metrics, spend, failures, limitations

---

## Do not spend time on

- Reintroducing Bing.
- A second camera zoom during RoomPlan.
- Sending raw walkthrough video through two frontier models as the count pipeline.
- Claiming 200k-spine production accuracy from this demo.
- Online weight updates after each survey.

---

## Blocking order

1. Camera-safe sequential capture (section 1)
2. Spine instances on a physical shelf (Invertis Library: 45 copies)
3. Identity / edition per copy (Invertis name-level + Pass C queue)
4. Price status per copy (Invertis name `web_search` drafts)
5. Fable and Astra on every copy in that survey; Jev as scorer (section 3)
6. RL with gold, reward, audit, promotion (section 4). Invertis logged the MDP (96 transitions, 0 labels); freeze/labels/promotion remain.
7. Product, security, accessibility, recorded demo (sections 5–6)
