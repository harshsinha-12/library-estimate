import pytest

from backend.app.evaluation.labeled import ZoneLabels
from eval.preflight import REQUIRED_CASE_TAGS, HoldoutRoster, validate_preflight


def _roster(*, status: str = "measured", survey_id: str = "survey-1") -> HoldoutRoster:
    copy_ids = [f"copy-{index:02d}" for index in range(50)]
    tags = sorted(REQUIRED_CASE_TAGS)
    return HoldoutRoster.model_validate({
        "roster_status": status,
        "roster_version": "1.0.0",
        "survey_id": survey_id,
        "split": "holdout",
        "frozen_by": "independent-operator",
        "frozen_at": "2026-09-22T00:00:00Z",
        "protocol_version": "controlled-zone-v1",
        "copy_ids": copy_ids,
        "case_tags": {
            copy_id: [tags[index]] if index < len(tags) else []
            for index, copy_id in enumerate(copy_ids)
        },
        "physical_evidence_refs": ["truth/roster.pdf"],
        "limitations": ["Rear covers could not be viewed in place."],
    })


def _labels(*, survey_id: str = "survey-1") -> ZoneLabels:
    cases = [
        {
            "case_id": f"present-{index:02d}",
            "metric": "copy_present",
            "asset_copy_id": f"copy-{index:02d}",
            "expected": True,
            "physical_evidence_ref": "truth/roster.pdf",
        }
        for index in range(50)
    ]
    cases.extend([
        {
            "case_id": "row-count",
            "metric": "row_count",
            "face_id": "face-1",
            "row_id": "row-1",
            "expected": 50,
            "physical_evidence_ref": "truth/roster.pdf",
        },
        {
            "case_id": "isbn",
            "metric": "copy_isbn",
            "asset_copy_id": "copy-00",
            "expected": "9780306406157",
            "physical_evidence_ref": "truth/barcode.jpg",
        },
        {
            "case_id": "excluded",
            "metric": "copy_excluded",
            "asset_copy_id": "copy-01",
            "expected": True,
            "physical_evidence_ref": "truth/mug.jpg",
        },
        {
            "case_id": "appraisal",
            "metric": "copy_appraisal",
            "asset_copy_id": "copy-02",
            "expected": True,
            "physical_evidence_ref": "truth/art.jpg",
        },
        {
            "case_id": "valuation-status",
            "metric": "copy_valuation_status",
            "asset_copy_id": "copy-02",
            "expected": "requires_appraisal",
            "physical_evidence_ref": "truth/art.jpg",
        },
        {
            "case_id": "same-isbn",
            "metric": "same_isbn_separate",
            "copy_ids": ["copy-03", "copy-04"],
            "expected": True,
            "physical_evidence_ref": "truth/same-isbn.jpg",
        },
    ])
    return ZoneLabels.model_validate({
        "survey_id": survey_id,
        "split": "holdout",
        "label_status": "measured",
        "labeled_by": "independent-operator",
        "cases": cases,
    })


def _report(*, survey_id: str = "survey-1") -> dict:
    return {
        "schema_version": "1.0.0",
        "survey_id": survey_id,
        "evaluation_split": "holdout",
        "generated_at": "2026-09-22T00:10:00Z",
        "package_hash": "a" * 64,
        "valuation": {},
        "spend": {"estimated_cost_usd": "1.250000"},
    }


def test_preflight_accepts_consistent_measured_holdout_without_claiming_accuracy() -> None:
    result = validate_preflight(_report(), _roster(), _labels())
    assert result["ready"] is True
    assert result["roster"]["copy_count"] == 50
    assert result["labels"]["metrics_present"] == {"numerator": 7, "denominator": 7}
    assert "does not establish device accuracy" in result["claim_boundary"]


def test_preflight_rejects_templates_and_survey_mismatch() -> None:
    with pytest.raises(ValueError, match="roster_status is template"):
        validate_preflight(_report(), _roster(status="template"), _labels())
    with pytest.raises(ValueError, match="survey_id mismatch"):
        validate_preflight(_report(), _roster(survey_id="survey-2"), _labels())


def test_preflight_requires_every_metric_and_roster_copy_to_be_labeled() -> None:
    labels = _labels()
    labels = labels.model_copy(update={
        "cases": [
            case
            for case in labels.cases
            if case.metric not in {"copy_isbn", "copy_valuation_status"}
            and case.asset_copy_id != "copy-49"
        ]
    })
    with pytest.raises(ValueError, match="holdout labels are missing metrics"):
        validate_preflight(_report(), _roster(), labels)
