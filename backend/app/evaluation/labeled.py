"""Compare a frozen report with independently recorded physical-zone labels."""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, ConfigDict, Field


class ZoneCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    metric: str = Field(pattern=(
        "^(row_count|copy_present|copy_isbn|copy_excluded|copy_appraisal|"
        "copy_valuation_status|same_isbn_separate)$"
    ))
    expected: str | int | bool
    face_id: str | None = None
    row_id: str | None = None
    asset_copy_id: str | None = None
    copy_ids: list[str] = Field(default_factory=list)
    physical_evidence_ref: str = Field(min_length=1)


class ZoneLabels(BaseModel):
    model_config = ConfigDict(extra="forbid")

    survey_id: str
    split: str = Field(pattern="^(train|holdout)$")
    labeled_by: str = Field(min_length=1)
    cases: list[ZoneCase] = Field(min_length=1)


def evaluate_zone(report: dict, labels: ZoneLabels) -> dict:
    if report.get("survey_id") != labels.survey_id:
        raise ValueError("label survey ID does not match report")
    copies = {
        row["asset_copy_id"]: row
        for row in (report.get("valuation") or {}).get("copies") or []
    }
    rows = {
        (row.get("face_id"), row.get("row_id")): row
        for row in (report.get("valuation") or {}).get("rows") or []
    }
    results = []
    for case in labels.cases:
        observed = None
        if case.metric == "row_count":
            row = rows.get((case.face_id, case.row_id))
            observed = row.get("detected_count") if row else None
        elif case.metric == "same_isbn_separate":
            selected = [copies.get(copy_id) for copy_id in case.copy_ids]
            observed = bool(
                len(case.copy_ids) >= 2
                and len(set(case.copy_ids)) == len(case.copy_ids)
                and all(selected)
                and len({row.get("isbn") for row in selected}) == 1
                and selected[0].get("isbn")
            )
        else:
            copy = copies.get(case.asset_copy_id or "")
            if case.metric == "copy_present":
                observed = copy is not None
            elif copy is not None:
                field = {
                    "copy_isbn": "isbn",
                    "copy_excluded": "excluded",
                    "copy_appraisal": "requires_appraisal",
                    "copy_valuation_status": "valuation_status",
                }[case.metric]
                observed = copy.get(field)
        results.append({
            "case_id": case.case_id, "metric": case.metric,
            "expected": case.expected, "observed": observed,
            "passed": observed == case.expected,
            "physical_evidence_ref": case.physical_evidence_ref,
        })
    groups = defaultdict(list)
    for result in results:
        groups[result["metric"]].append(result)
    return {
        "survey_id": labels.survey_id, "split": labels.split,
        "labeled_by": labels.labeled_by,
        "overall": {
            "numerator": sum(row["passed"] for row in results),
            "denominator": len(results),
        },
        "metrics": {
            metric: {
                "numerator": sum(row["passed"] for row in members),
                "denominator": len(members),
            }
            for metric, members in sorted(groups.items())
        },
        "cases": results,
        "limitations": [
            "Labels require an independent physical roster and evidence references.",
            "Cases omitted from the label file are outside these denominators.",
        ],
    }
