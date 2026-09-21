# Leftover work

**Date:** 2026-09-22  
**Sources:** `question.md`, `IMPLEMENTATION.md`, `FINAL-PLAN.md`, `CHECKPOINTS.md`, `SESSION-RUN.md`, and the current iOS/backend/CV code. Read question.md and maybe other files if doubt again.

Harsh will most likely be sleeping, or lying on the bed with the laptop open, and working through remote access in Cursor or Codex via his phone. Keep this plan. Do not wait for a rewrite of the leftover list.

Price discovery stays on the OpenAI Responses API `web_search` tool already in this repo (`user_location` from survey geography, ISBN then name, batches of unique unpriced objects). That path is fine. Do not add the Bing Search API. Do not scrape Amazon.

Do not run the physical device, the library scan, or any other step that needs Harsh present until he is back. Coding, unit tests, fixture gates, and other checks that do not need his permission should proceed. If a step needs his permission, the phone, a live survey on device, or a decision only he can make, skip it and leave it for when he returns.

This is the remaining build. Fixture tests and adapters do not close an item. A checked Stage 1–4 clock in `CHECKPOINTS.md` means the original fixture gate passed; the physical row and the items below are still open.

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

That rule is proven on labeled JSON. It is not proven on a phone. Live detection is `VNDetectRectanglesRequest` (max 24 boxes, tall + thin + three letters). Merge is image-x ±0.06, not shelf-face coordinates. Coverage increments by frame count. Live ISBN is never written. Outlines are the current frame only and are not tappable.

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

### Physical Stage 4 row gate

17. One physical row of 8–10 books, recorded in `SESSION-RUN.md`:

    - detected / actual
    - one stable copy per visible spine
    - reverse sweep does not double
    - each copy has ISBN/name **or** a visible Pass C task
    - each eligible copy has a reviewed local physical range **or** pending/no-comparable
    - drafts are never shown as confirmed prices
    - priced/eligible as numerator/denominator

Until this row is recorded, inventory, models, and the demo are running on fixtures. No sealed phone survey so far has shelf observations.

### Cost at scale

Honest for $50 and 50–100 books. Not a 200k-spine production claim.

18. Detect and track on device. Do not send every frame to a model.
19. Price search once per unique edition + market; reuse evidence per copy. That rule must hold on the phone row.
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
25. Confirm live model IDs (still open in `SESSION-RUN.md`) and actually spend against the ledger. Adapters with no live call do not count.
26. Models still must not write geometry, ISBN, merge, or money.

---

## 4. RL

`backend/app/rl/offline.py` has a reward table (false merge −5, missed high-value −8, mug exclusion +0.2, and the rest of the §13 weights). Every live transition is stored with `reward: null`. Independent labels are a POST that has not been done on a real survey. Auto-accepts are not audited. `approved_at` is always null. Specialist heads (condition, eligibility, damage, duplicate-features, quality) train on synthetic labels and are never used or credited live.

Left:

27. Write an `RLTransition` for every material decision: accept, recapture, human, specialist, frontier, price confirm, barcode rescan — not only the model-replay button.
28. Apply the §13 reward from independent labels, never from “Jev agreed with Fable.”
29. Audit log of auto-accepted items: copy id, evidence hash, A/B/Jev, policy reason, who could still overturn it.
30. Gold set: the labeled 50–100 book zone with every demo case (two same-ISBN copies, reverse scan, no ISBN, ambiguous edition, moved book, damaged book, portrait + spoken damage, mug, appraisal item). Freeze it before any training.
31. Classification credit: condition / eligibility / damage / keep-vs-merge heads are scored against gold, and that score is part of reward. A router-only bandit is not “your own models.”
32. Recapture is sequential: action → new evidence → `next_state_id`. Today `next_state_id` is always null.
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

40. HTTPS in front of the API before an operator token is used. Phone packages encrypted at rest. Existing R2 plaintext migrated or left on a non-encrypted namespace on purpose. Face/bystander redaction hook. Retention execute path only after dry-run review.
41. Accessibility: Dynamic Type, VoiceOver through capture, status not color-only, large one-handed targets, transcripts for TTS.
42. Holdout survey, never used for training, metrics with numerator/denominator via `scripts/evaluate_stage5.py` on a real report plus an independent roster.
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
2. Real spine instances on one physical row (section 2, through item 17)
3. Identity / edition per copy
4. Price status per copy
5. Fable and Astra on every copy in that survey; Jev as scorer (section 3)
6. RL with gold, reward, audit, promotion (section 4)
7. Product, security, accessibility, recorded demo (sections 5–6)

Everything after step 2 is polish on a library that still has not been counted.
