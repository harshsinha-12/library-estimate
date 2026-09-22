# Evaluation workspace

Stage-specific labeled fixtures, predictions, and metric outputs live here. Stage 1 uses the schema and geometry fixtures in `fixtures/stage1`; the controlled shelf holdout is added in Stage 2 without treating synthetic or preview output as measured accuracy.

## Stage 5 holdout

`holdout-roster.template.json` and `holdout-labels.template.json` are templates only.
Their placeholder values are not observations, and both are deliberately marked
`template`. Copy them into `eval/runs/<run-id>/truth/`, replace every placeholder, expand
the roster to 50–100 unique physical copies, and set the status fields to `measured` only
after an independent operator records the physical evidence.

Before evaluation, validate report/roster/label consistency:

```sh
python3 -m eval.preflight \
  --report eval/runs/<run-id>/report/report.json \
  --roster eval/runs/<run-id>/truth/holdout-roster.json \
  --labels eval/runs/<run-id>/truth/holdout-labels.json \
  --output eval/runs/<run-id>/evaluation/preflight.json
```

The validator rejects templates, duplicate roster IDs, malformed or duplicate label
cases, incomplete metric coverage, missing report provenance, inconsistent/non-holdout split
metadata, and survey-ID mismatches. A successful preflight is a consistency check, not
an accuracy result.

After preflight succeeds:

```sh
python3 -m scripts.evaluate_stage5 \
  eval/runs/<run-id>/report/report.json \
  eval/runs/<run-id>/truth/holdout-labels.json \
  --output eval/runs/<run-id>/evaluation/metrics.json
```

Use `LIDAR-DEMO-RUNBOOK.md` as the single 12-step physical-device demo checklist.

