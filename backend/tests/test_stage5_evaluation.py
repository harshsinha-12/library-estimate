from backend.app.evaluation.labeled import ZoneLabels, evaluate_zone


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
