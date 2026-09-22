"""Compare a frozen report with independently recorded physical-zone labels."""

from __future__ import annotations

import json
from collections import defaultdict
from hashlib import sha256
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SUPPORTED_METRICS = (
    "row_count",
    "copy_present",
    "copy_isbn",
    "copy_excluded",
    "copy_appraisal",
    "copy_valuation_status",
    "same_isbn_separate",
)
EVALUATOR_VERSION = "1.0.0"


def _nonempty(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


class ZoneCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    metric: Literal[
        "row_count",
        "copy_present",
        "copy_isbn",
        "copy_excluded",
        "copy_appraisal",
        "copy_valuation_status",
        "same_isbn_separate",
    ]
    expected: str | int | bool
    face_id: str | None = None
    row_id: str | None = None
    asset_copy_id: str | None = None
    copy_ids: list[str] = Field(default_factory=list)
    physical_evidence_ref: str = Field(min_length=1)

    @field_validator(
        "case_id", "face_id", "row_id", "asset_copy_id", "physical_evidence_ref"
    )
    @classmethod
    def validate_strings(cls, value: str | None) -> str | None:
        return _nonempty(value) if value is not None else None

    @field_validator("copy_ids")
    @classmethod
    def validate_copy_ids(cls, values: list[str]) -> list[str]:
        normalized = [_nonempty(value) for value in values]
        if len(normalized) != len(set(normalized)):
            raise ValueError("copy_ids must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_metric_shape(self) -> ZoneCase:
        if self.metric == "row_count":
            if self.face_id is None or self.row_id is None:
                raise ValueError("row_count requires face_id and row_id")
            if self.asset_copy_id is not None or self.copy_ids:
                raise ValueError("row_count cannot include asset_copy_id or copy_ids")
            if isinstance(self.expected, bool) or not isinstance(self.expected, int):
                raise ValueError("row_count expected must be an integer")
            if self.expected < 0:
                raise ValueError("row_count expected must be non-negative")
            return self

        if self.metric == "same_isbn_separate":
            if len(self.copy_ids) < 2:
                raise ValueError("same_isbn_separate requires at least two unique copy_ids")
            if (
                self.face_id is not None
                or self.row_id is not None
                or self.asset_copy_id is not None
            ):
                raise ValueError(
                    "same_isbn_separate cannot include face_id, row_id, or asset_copy_id"
                )
            if not isinstance(self.expected, bool):
                raise ValueError("same_isbn_separate expected must be boolean")
            return self

        if self.asset_copy_id is None:
            raise ValueError(f"{self.metric} requires asset_copy_id")
        if self.face_id is not None or self.row_id is not None or self.copy_ids:
            raise ValueError(f"{self.metric} cannot include face_id, row_id, or copy_ids")
        expected_types: dict[str, type] = {
            "copy_present": bool,
            "copy_isbn": str,
            "copy_excluded": bool,
            "copy_appraisal": bool,
            "copy_valuation_status": str,
        }
        expected_type = expected_types[self.metric]
        if not isinstance(self.expected, expected_type):
            expected_name = "boolean" if expected_type is bool else expected_type.__name__
            raise ValueError(f"{self.metric} expected must be {expected_name}")
        if isinstance(self.expected, str) and not self.expected.strip():
            raise ValueError(f"{self.metric} expected must not be blank")
        return self

    def identity(self) -> tuple[Any, ...]:
        locator: tuple[Any, ...]
        if self.metric == "row_count":
            locator = (self.face_id, self.row_id)
        elif self.metric == "same_isbn_separate":
            locator = tuple(sorted(self.copy_ids))
        else:
            locator = (self.asset_copy_id,)
        return (self.metric, *locator)


class ZoneLabels(BaseModel):
    model_config = ConfigDict(extra="forbid")

    survey_id: str = Field(min_length=1)
    split: Literal["train", "holdout"]
    label_status: Literal["template", "measured"] = "measured"
    labeled_by: str = Field(min_length=1)
    cases: list[ZoneCase] = Field(min_length=1)

    @field_validator("survey_id", "labeled_by")
    @classmethod
    def validate_strings(cls, value: str) -> str:
        return _nonempty(value)

    @model_validator(mode="after")
    def validate_unique_cases(self) -> ZoneLabels:
        case_ids = [case.case_id for case in self.cases]
        duplicate_ids = sorted({value for value in case_ids if case_ids.count(value) > 1})
        if duplicate_ids:
            raise ValueError(f"duplicate case_id values: {', '.join(duplicate_ids)}")
        identities = [case.identity() for case in self.cases]
        duplicate_cases = sorted({
            case.case_id
            for case in self.cases
            if identities.count(case.identity()) > 1
        })
        if duplicate_cases:
            raise ValueError(
                "duplicate metric/target cases would double-count the denominator: "
                + ", ".join(duplicate_cases)
            )
        return self


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(encoded.encode()).hexdigest()


def _indexed_rows(report: dict, labels: ZoneLabels) -> tuple[dict[str, dict], dict[tuple, dict]]:
    valuation = report.get("valuation") or {}
    if not isinstance(valuation, dict):
        raise ValueError("report valuation must be an object")
    raw_copies = valuation.get("copies") or []
    raw_rows = valuation.get("rows") or []
    if not isinstance(raw_copies, list):
        raise ValueError("report valuation.copies must be an array")
    if not isinstance(raw_rows, list):
        raise ValueError("report valuation.rows must be an array")

    copies: dict[str, dict] = {}
    for index, row in enumerate(raw_copies):
        if not isinstance(row, dict):
            raise ValueError(f"report valuation.copies[{index}] must be an object")
        copy_id = row.get("asset_copy_id")
        if not isinstance(copy_id, str) or not copy_id.strip():
            raise ValueError(
                f"report valuation.copies[{index}].asset_copy_id must be a non-blank string"
            )
        if copy_id in copies:
            raise ValueError(f"duplicate report asset_copy_id: {copy_id}")
        copies[copy_id] = row

    rows: dict[tuple, dict] = {}
    for index, row in enumerate(raw_rows):
        if not isinstance(row, dict):
            raise ValueError(f"report valuation.rows[{index}] must be an object")
        key = (row.get("face_id"), row.get("row_id"))
        if not all(isinstance(value, str) and value.strip() for value in key):
            raise ValueError(
                f"report valuation.rows[{index}] requires non-blank face_id and row_id"
            )
        if key in rows:
            raise ValueError(f"duplicate report row target: face_id={key[0]}, row_id={key[1]}")
        rows[key] = row

    report_split = report.get("evaluation_split")
    if report_split is not None and report_split != labels.split:
        raise ValueError(
            f"label split {labels.split!r} does not match report evaluation_split "
            f"{report_split!r}"
        )
    return copies, rows


def evaluate_zone(report: dict, labels: ZoneLabels) -> dict:
    if not isinstance(report, dict):
        raise ValueError("report must be a JSON object")
    if labels.label_status != "measured":
        raise ValueError(
            "template labels cannot be evaluated; record independent measurements first"
        )
    if report.get("survey_id") != labels.survey_id:
        raise ValueError(
            f"label survey_id {labels.survey_id!r} does not match report survey_id "
            f"{report.get('survey_id')!r}"
        )
    copies, rows = _indexed_rows(report, labels)
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
            "score": {
                "numerator": int(observed == case.expected),
                "denominator": 1,
            },
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
        "provenance": {
            "evaluator": "backend.app.evaluation.labeled.evaluate_zone",
            "evaluator_version": EVALUATOR_VERSION,
            "report": {
                "survey_id": report.get("survey_id"),
                "schema_version": report.get("schema_version"),
                "generated_at": report.get("generated_at"),
                "package_hash": report.get("package_hash"),
                "canonical_sha256": _canonical_sha256(report),
            },
            "labels": {
                "survey_id": labels.survey_id,
                "split": labels.split,
                "label_status": labels.label_status,
                "canonical_sha256": _canonical_sha256(labels.model_dump(mode="json")),
            },
        },
        "limitations": [
            "Labels require an independent physical roster and evidence references.",
            "Cases omitted from the label file are outside these denominators.",
        ],
    }
