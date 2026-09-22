# LiDAR demo runbook — canonical 12-step checklist

This is an operator checklist, not a result record. Empty boxes and placeholders are not
evidence of completion or measured device accuracy. Use one new 50–100-copy controlled
zone, keep its holdout labels independent, and do not tune after viewing holdout output.

## Run identity and evidence root

- Run ID: `________________`
- Survey ID: `________________`
- UTC start/end: `________________` / `________________`
- Device and OS: `________________`
- App/backend commit SHA: `________________`
- iOS app version/build: `________________`
- Backend/schema/report versions: `________________`
- Capture protocol/roster version: `________________`
- Pipeline A model/prompt version: `________________`
- Pipeline B model/prompt version: `________________`
- Jev model/prompt version: `________________`
- Deterministic policy ID/version: `________________`
- Catalog/pricing adapter and source versions: `________________`
- Operator / independent labeler: `________________` / `________________`
- Artifact root: `eval/runs/<run-id>/`
- Continuous screen recording: `eval/runs/<run-id>/recordings/full-demo.mov`

Before Step 1, copy and complete `eval/holdout-roster.template.json` and
`eval/holdout-labels.template.json` under the artifact root. Preserve original evidence;
do not edit generated reports or metrics.

## Checklist

1. **RoomPlan geometry, location, and guidance**
   - [ ] Show the LiDAR room pass, 2D/3D geometry, multi-pass guidance, and the survey's
     city/market source.
   - Evidence: `capture/package/`, `derived/geometry.json`, `recordings/step-01.mov`
   - Record failures/limitations: `notes/step-01.md`

2. **Coverage warning and targeted recapture**
   - [ ] Deliberately leave coverage incomplete, show the warning, then capture the
     requested face/row without replacing original evidence.
   - Evidence: `derived/coverage.json`, `evidence/recaptures/`, `recordings/step-02.mov`
   - Record failures/limitations: `notes/step-02.md`

3. **Physical count before identity**
   - [ ] Show row and total physical-copy counts before ISBN/catalog resolution.
   - Evidence: `derived/inventory-pre-identity.json`, `truth/holdout-roster.json`,
     `recordings/step-03.mov`
   - Record detected/actual as numerator/denominator: `notes/step-03.md`

4. **Opposite-direction repeat scan**
   - [ ] Scan the selected shelf left-to-right and right-to-left; show that the second
     pass does not double the physical count.
   - Evidence: `evidence/reverse-scan/`, `derived/tracks.json`, `recordings/step-04.mov`
   - Record retained distinct copies/actual copies: `notes/step-04.md`

5. **Same-ISBN copies remain separate**
   - [ ] Show two physical copies with the same ISBN retaining distinct `asset_copy_id`
     values.
   - Evidence: `truth/holdout-roster.json`, `derived/inventory.json`,
     `recordings/step-05.mov`
   - Record passed/checked cases: `notes/step-05.md`

6. **ISBN validation and catalog evidence**
   - [ ] Show a valid barcode/ISBN, a missing or invalid ISBN, and an ambiguous edition
     resolved only with rear-cover/title-page evidence.
   - Evidence: `derived/identity.json`, `evidence/identity/`, `recordings/step-06.mov`
   - Record exact/resolved and unresolved/eligible denominators: `notes/step-06.md`

7. **OpenAI web-search pricing evidence**
   - [ ] Use the OpenAI Responses API `web_search` tool by ISBN and then by name where
     needed; show citation URLs, market, timestamp, exclusions, and the
     operator-confirmed physical-book range. Do not use Bing or scrape Amazon.
   - Evidence: `derived/pricing.json`, `evidence/pricing/`, `recordings/step-07.mov`
   - Record priced/eligible numerator/denominator: `notes/step-07.md`

8. **Portrait note and damage close-up**
   - [ ] Show the spoken portrait-damage note linked to the intended asset and immutable
     close-up evidence.
   - Evidence: `evidence/audio/`, `evidence/damage/`, `derived/notes.json`,
     `recordings/step-08.mov`
   - Record linked/expected numerator/denominator: `notes/step-08.md`

9. **Exclusion and appraisal routing**
   - [ ] Show the mug inventoried but excluded from valuation and the designated item
     routed to specialist appraisal rather than auto-finalized.
   - Evidence: `derived/inventory.json`, `derived/pricing.json`,
     `recordings/step-09.mov`
   - Record passed/checked cases: `notes/step-09.md`

10. **Pipeline disagreement and guarded review**
    - [ ] Show Pipeline A/B outputs over the same sealed evidence, provider/model/prompt
      versions, disagreement, Jev proposal, deterministic gate, and human decision.
    - Evidence: `model/model-runs.json`, `model/transitions.json`,
      `recordings/step-10.mov`
    - Record every provider failure/fallback: `failures/provider-failures.json`

11. **Reviewable outputs and audit**
    - [ ] Show 2D plan, 3D model, inventory, contents/building ranges, unresolved
      coverage, evidence links, audit history, and JSON/PDF reports.
    - Evidence: `report/report.json`, `report/report.pdf`, `audit/audit.json`,
      `recordings/step-11.mov`
    - Record report/export failures and limitations: `notes/step-11.md`

12. **Independent evaluation, spend, and claim boundary**
    - [ ] Run preflight, then evaluation only after preflight reports `ready: true`.
    - [ ] Show every metric with numerator/denominator, unresolved coverage, actual
      ledger spend, failures, limitations, and this run's frozen versions.
    - Commands:
      `python3 -m eval.preflight --report eval/runs/<run-id>/report/report.json --roster eval/runs/<run-id>/truth/holdout-roster.json --labels eval/runs/<run-id>/truth/holdout-labels.json --output eval/runs/<run-id>/evaluation/preflight.json`
      and
      `python3 -m scripts.evaluate_stage5 eval/runs/<run-id>/report/report.json eval/runs/<run-id>/truth/holdout-labels.json --output eval/runs/<run-id>/evaluation/metrics.json`
    - Evidence: `evaluation/preflight.json`, `evaluation/metrics.json`,
      `cost/usage-ledger.json`, `failures/provider-failures.json`,
      `limitations/limitations.md`, `versions/versions.json`,
      `recordings/step-12.mov`
    - [ ] Confirm no template, fixture, simulator, preview, or synthetic result is
      described as physical-device accuracy.

## Closeout

- [ ] The full recording opens and visibly covers all 12 steps.
- [ ] `report.json`, labels, roster, preflight, and metrics have the same survey ID and
  holdout split.
- [ ] Input/output SHA-256 provenance is present in preflight and metrics.
- [ ] Spend is copied from the run ledger, including failed calls; no amount is guessed.
- [ ] Every failure and limitation is retained; no unresolved case is dropped from a
  denominator after results are viewed.
- [ ] Artifact root is archived read-only at: `________________`
- Operator sign-off / UTC: `________________`
- Independent labeler sign-off / UTC: `________________`
