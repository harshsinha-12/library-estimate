import json
import sys

import pytest
from pydantic import ValidationError

from backend.app.evaluation.labeled import ZoneLabels, evaluate_zone
from scripts.evaluate_stage5 import main


def test_labeled_evaluation_uses_only_supplied_case_denominators() -> None:
    report = {
        "survey_id": "survey-1",
        "valuation": {
            "rows": [{"face_id": "face-a", "row_id": "row-1", "detected_count": 8}],
            "copies": [
                {"asset_copy_id": "copy-a", "isbn": "9780306406157", "excluded": False},
                {"asset_copy_id": "copy-b", "isbn": "9780306406157", "excluded": False},
            ],
        },
    }
    labels = ZoneLabels.model_validate({
        "survey_id": "survey-1", "split": "holdout", "labeled_by": "operator",
        "cases": [
            {"case_id": "count", "metric": "row_count", "face_id": "face-a",
             "row_id": "row-1", "expected": 9, "physical_evidence_ref": "roster/page-1"},
            {"case_id": "two copies", "metric": "same_isbn_separate",
             "copy_ids": ["copy-a", "copy-b"], "expected": True,
             "physical_evidence_ref": "roster/page-1"},
            {"case_id": "missing", "metric": "copy_present", "asset_copy_id": "copy-c",
             "expected": True, "physical_evidence_ref": "roster/page-1"},
        ],
    })
    result = evaluate_zone(report, labels)
    assert result["overall"] == {"numerator": 1, "denominator": 3}
    assert result["metrics"]["row_count"] == {"numerator": 0, "denominator": 1}
    assert result["cases"][0]["observed"] == 8
    assert result["cases"][0]["score"] == {"numerator": 0, "denominator": 1}
    assert result["provenance"]["report"]["survey_id"] == "survey-1"
    assert len(result["provenance"]["report"]["canonical_sha256"]) == 64


def test_labels_reject_duplicate_and_malformed_cases_clearly() -> None:
    base = {
        "survey_id": "survey-1",
        "split": "holdout",
        "labeled_by": "operator",
    }
    with pytest.raises(ValidationError, match="duplicate case_id"):
        ZoneLabels.model_validate({
            **base,
            "cases": [
                {
                    "case_id": "duplicate",
                    "metric": "copy_present",
                    "asset_copy_id": "copy-a",
                    "expected": True,
                    "physical_evidence_ref": "roster/1",
                },
                {
                    "case_id": "duplicate",
                    "metric": "copy_present",
                    "asset_copy_id": "copy-b",
                    "expected": True,
                    "physical_evidence_ref": "roster/2",
                },
            ],
        })
    with pytest.raises(ValidationError, match="row_count requires face_id and row_id"):
        ZoneLabels.model_validate({
            **base,
            "cases": [{
                "case_id": "bad-row",
                "metric": "row_count",
                "expected": 2,
                "physical_evidence_ref": "roster/1",
            }],
        })
    with pytest.raises(ValidationError, match="expected must be boolean"):
        ZoneLabels.model_validate({
            **base,
            "cases": [{
                "case_id": "bad-present",
                "metric": "copy_present",
                "asset_copy_id": "copy-a",
                "expected": 1,
                "physical_evidence_ref": "roster/1",
            }],
        })


def test_evaluation_rejects_templates_split_mismatch_and_duplicate_report_targets() -> None:
    case = {
        "case_id": "present",
        "metric": "copy_present",
        "asset_copy_id": "copy-a",
        "expected": True,
        "physical_evidence_ref": "roster/1",
    }
    template = ZoneLabels.model_validate({
        "survey_id": "survey-1",
        "split": "holdout",
        "label_status": "template",
        "labeled_by": "operator",
        "cases": [case],
    })
    with pytest.raises(ValueError, match="template labels cannot be evaluated"):
        evaluate_zone({"survey_id": "survey-1"}, template)

    labels = template.model_copy(update={"label_status": "measured"})
    with pytest.raises(ValueError, match="evaluation_split"):
        evaluate_zone(
            {"survey_id": "survey-1", "evaluation_split": "train"},
            labels,
        )
    with pytest.raises(ValueError, match="duplicate report asset_copy_id"):
        evaluate_zone(
            {
                "survey_id": "survey-1",
                "valuation": {
                    "copies": [
                        {"asset_copy_id": "copy-a"},
                        {"asset_copy_id": "copy-a"},
                    ]
                },
            },
            labels,
        )


def test_evaluation_cli_writes_input_provenance(tmp_path, monkeypatch) -> None:
    report_path = tmp_path / "report.json"
    labels_path = tmp_path / "labels.json"
    output_path = tmp_path / "metrics.json"
    report_path.write_text(json.dumps({
        "schema_version": "1.0.0",
        "survey_id": "survey-1",
        "generated_at": "2026-09-22T00:00:00Z",
        "package_hash": "a" * 64,
        "valuation": {"copies": [{"asset_copy_id": "copy-a"}]},
    }))
    labels_path.write_text(json.dumps({
        "survey_id": "survey-1",
        "split": "holdout",
        "label_status": "measured",
        "labeled_by": "operator",
        "cases": [{
            "case_id": "present",
            "metric": "copy_present",
            "asset_copy_id": "copy-a",
            "expected": True,
            "physical_evidence_ref": "truth/roster.pdf",
        }],
    }))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "evaluate_stage5",
            str(report_path),
            str(labels_path),
            "--output",
            str(output_path),
        ],
    )
    main()
    result = json.loads(output_path.read_text())
    assert result["overall"] == {"numerator": 1, "denominator": 1}
    assert result["provenance"]["input_files"]["report"]["path"] == str(report_path)
    assert len(result["provenance"]["input_files"]["labels"]["sha256"]) == 64
