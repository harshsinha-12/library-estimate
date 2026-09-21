# Stage 5 validation and operator run

## Current verification boundary

The model adapters, cost ledger, review gates, offline trainer, security controls, report, and iOS screens can be tested without live Anthropic calls. A generic iOS Simulator build verifies compilation only. The labeled holdout and demo metrics require a new physical capture and an independent manual roster; fixture results must not be reported as device accuracy.

## Security setup before a private network run

Set `LIBRARY_REQUIRE_AUTH=1`, a long random `LIBRARY_OPERATOR_TOKEN`, and a base64 32-byte `LIBRARY_DATA_ENCRYPTION_KEY` on a fresh storage namespace. The iOS operator token lives in the device Keychain. It is sent only to an HTTPS backend URL. The default local HTTP URL remains useful for an unauthenticated development run, but must not be used with an operator token. Put an HTTPS reverse proxy in front of the API for a private run and keep R2 private. Existing plaintext objects need a migration before enabling object encryption on the same namespace.

`GET /v1/surveys/{id}/evidence?path=...` is the authenticated local equivalent of a signed download URL. Only uploaded package paths and server-derived paths are eligible. Responses have `Cache-Control: no-store`. Bounded access logs omit query strings, token values, and literal survey UUIDs. `DELETE /v1/surveys/{id}` requires `X-Confirm-Delete: {id}`; it removes survey objects, survey-scoped Redis keys, jobs, create/upload/seal idempotency records, and policies trained from that survey. This action is irreversible. `scripts/retention_cleanup.py` lists expired surveys by default and deletes only with `--execute`; review its dry run before using it on real data.

Object encryption does not cover a sealed package already stored on the phone, transport through a plain HTTP development server, or third-party provider retention. Face/bystander redaction and a production key rotation/migration remain open.

## Independent zone and holdout

1. Select a controlled 50–100-book zone and freeze the capture protocol, model IDs, prompts, and policy version. Record the physical roster separately from the app. Include two same-ISBN copies, opposite-direction repeat scan, missing ISBN, ambiguous edition, moved book, damaged book, portrait note and close-up, excluded mug, and appraisal item.
2. Capture the zone and record row actual counts, distinct physical copy IDs, ISBNs, excluded items, appraisal items, source photos, and unresolved items. Record physical evidence paths in a separate label JSON file. Use a new, unseen survey for `split: holdout`; do not retune on it.
3. Fetch `/v1/surveys/{id}/report` and save the JSON. Create `ZoneLabels` JSON with `survey_id`, `split`, `labeled_by`, and `cases`. Each case needs `case_id`, `metric`, `expected`, and `physical_evidence_ref`. Supported metrics are `row_count` (with `face_id` and `row_id`), `copy_present`, `copy_isbn`, `copy_excluded`, `copy_appraisal`, `copy_valuation_status` (with `asset_copy_id`), and `same_isbn_separate` (with `copy_ids`).
4. From the repository root, run `python3 -m scripts.evaluate_stage5 report.json labels.json --output metrics.json`. The output includes numerator and denominator for every supplied metric and every case's observed value. Omitted cases are outside the denominator. Archive the report, labels, metrics, model runs, transitions, usage ledger, and provider failures together.
5. Add independent transition labels via `POST /v1/surveys/{id}/independent-labels`. After at least 10 labeled training transitions, call `POST /v1/policies/train` with disjoint training and holdout survey IDs. `POST /v1/policies/{id}/shadow` scores logged holdout actions only; inspect its action support and overlap warning. It never updates live routing.

The Stage 4 physical 8–10 book row walkthrough and the full Stage 5 demo script in `FINAL-PLAN.md` §23 still require the operator and the phone. Record detected/actual and priced/eligible with numerator and denominator in `SESSION-RUN.md` before closing either stage gate.
