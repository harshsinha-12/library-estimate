"""Validate holdout artifacts before running Stage 5 evaluation.

This checks readiness and provenance only. It does not calculate or claim device accuracy.
"""

from __future__ import annotations

import argparse
import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from backend.app.evaluation.labeled import SUPPORTED_METRICS, ZoneLabels

REQUIRED_CASE_TAGS = {
    "ambiguous_edition",
    "appraisal_item",
    "damaged_book",
    "excluded_mug",
    "moved_book",
    "no_isbn",
    "portrait_note_closeup",
    "reverse_scan",
    "same_isbn_two_copies",
}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _nonempty(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be blank")
    return value


class HoldoutRoster(BaseModel):
    """Frozen physical roster kept independently from model output."""

    model_config = ConfigDict(extra="forbid")

    roster_status: Literal["template", "measured"]
    roster_version: str = Field(min_length=1)
    survey_id: str = Field(min_length=1)
    split: Literal["holdout"]
    frozen_by: str = Field(min_length=1)
    frozen_at: str = Field(min_length=1)
    protocol_version: str = Field(min_length=1)
    copy_ids: list[str]
    case_tags: dict[str, list[str]]
    physical_evidence_refs: list[str]
    limitations: list[str]

    @field_validator(
        "roster_version",
        "survey_id",
        "frozen_by",
        "frozen_at",
        "protocol_version",
    )
    @classmethod
    def validate_strings(cls, value: str) -> str:
        return _nonempty(value)

    @field_validator("copy_ids", "physical_evidence_refs", "limitations")
    @classmethod
    def validate_string_lists(cls, values: list[str]) -> list[str]:
        return [_nonempty(value) for value in values]

    @model_validator(mode="after")
    def validate_roster(self) -> HoldoutRoster:
        if len(self.copy_ids) != len(set(self.copy_ids)):
            raise ValueError("copy_ids contains duplicates")
        unknown_ids = sorted(set(self.case_tags) - set(self.copy_ids))
        if unknown_ids:
            raise ValueError(
                "case_tags contains IDs absent from copy_ids: " + ", ".join(unknown_ids)
            )
        covered_tags = {tag for tags in self.case_tags.values() for tag in tags}
        unknown_tags = sorted(covered_tags - REQUIRED_CASE_TAGS)
        if unknown_tags:
            raise ValueError("case_tags contains unsupported tags: " + ", ".join(unknown_tags))
        if self.roster_status == "measured":
            if not 50 <= len(self.copy_ids) <= 100:
                raise ValueError("measured holdout roster must contain 50-100 unique copy_ids")
            missing_tags = sorted(REQUIRED_CASE_TAGS - covered_tags)
            if missing_tags:
                raise ValueError(
                    "measured holdout roster is missing case tags: "
                    + ", ".join(missing_tags)
                )
            if not self.physical_evidence_refs:
                raise ValueError("measured holdout roster requires physical_evidence_refs")
            if not self.limitations:
                raise ValueError("measured holdout roster requires explicit limitations")
        return self


def _read_json(path: Path, description: str) -> tuple[dict, str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read {description} {path}: {exc}") from exc
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{description} is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{description} root must be a JSON object")
    return value, sha256(raw).hexdigest()


def validate_preflight(report: dict, roster: HoldoutRoster, labels: ZoneLabels) -> dict:
    """Return a readiness receipt or raise a specific validation error."""

    if roster.roster_status != "measured":
        raise ValueError(
            "roster_status is template; replace it with an independently measured roster"
        )
    if labels.label_status != "measured":
        raise ValueError("label_status is template; replace it with independent measurements")
    survey_ids = {
        "report": report.get("survey_id"),
        "roster": roster.survey_id,
        "labels": labels.survey_id,
    }
    if len(set(survey_ids.values())) != 1:
        detail = ", ".join(f"{name}={value!r}" for name, value in survey_ids.items())
        raise ValueError(f"survey_id mismatch: {detail}")
    if roster.split != labels.split:
        raise ValueError(f"split mismatch: roster={roster.split!r}, labels={labels.split!r}")
    report_split = report.get("evaluation_split")
    if report_split is not None and report_split != labels.split:
        raise ValueError(
            f"split mismatch: report evaluation_split={report_split!r}, labels={labels.split!r}"
        )
    if labels.split != "holdout":
        raise ValueError("Stage 5 preflight requires split='holdout'")

    required_report_fields = (
        "schema_version",
        "generated_at",
        "package_hash",
        "valuation",
        "spend",
    )
    missing_fields = [
        field for field in required_report_fields if report.get(field) in (None, "")
    ]
    if missing_fields:
        raise ValueError("report is missing provenance fields: " + ", ".join(missing_fields))
    if not isinstance(report["valuation"], dict):
        raise ValueError("report valuation must be an object")
    if not isinstance(report["spend"], dict):
        raise ValueError("report spend must be an object")
    if not isinstance(report["package_hash"], str) or not _SHA256.fullmatch(
        report["package_hash"]
    ):
        raise ValueError("report package_hash must be a lowercase SHA-256 hex digest")

    present_metrics = {case.metric for case in labels.cases}
    missing_metrics = sorted(set(SUPPORTED_METRICS) - present_metrics)
    if missing_metrics:
        raise ValueError("holdout labels are missing metrics: " + ", ".join(missing_metrics))
    labeled_copy_ids = {
        case.asset_copy_id
        for case in labels.cases
        if case.asset_copy_id is not None
    } | {
        copy_id
        for case in labels.cases
        for copy_id in case.copy_ids
    }
    unlabeled_copy_ids = sorted(set(roster.copy_ids) - labeled_copy_ids)
    if unlabeled_copy_ids:
        sample = ", ".join(unlabeled_copy_ids[:5])
        suffix = " ..." if len(unlabeled_copy_ids) > 5 else ""
        raise ValueError(
            f"{len(unlabeled_copy_ids)} roster copy_ids have no label case: {sample}{suffix}"
        )

    return {
        "ready": True,
        "claim_boundary": (
            "Preflight validates artifact consistency only; it does not establish device accuracy."
        ),
        "survey_id": labels.survey_id,
        "split": labels.split,
        "roster": {
            "copy_count": len(roster.copy_ids),
            "required_case_tags": {
                "numerator": len(REQUIRED_CASE_TAGS),
                "denominator": len(REQUIRED_CASE_TAGS),
            },
        },
        "labels": {
            "case_count": len(labels.cases),
            "metrics_present": {
                "numerator": len(present_metrics),
                "denominator": len(SUPPORTED_METRICS),
            },
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate measured Stage 5 report, roster, and label artifacts."
    )
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--roster", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        report, report_hash = _read_json(args.report, "report")
        roster_data, roster_hash = _read_json(args.roster, "roster")
        labels_data, labels_hash = _read_json(args.labels, "labels")
        roster = HoldoutRoster.model_validate(roster_data)
        labels = ZoneLabels.model_validate(labels_data)
        result = validate_preflight(report, roster, labels)
    except (ValidationError, ValueError) as exc:
        parser.error(str(exc))

    result["provenance"] = {
        "validator": "eval.preflight",
        "input_files": {
            "report": {"path": str(args.report), "sha256": report_hash},
            "roster": {"path": str(args.roster), "sha256": roster_hash},
            "labels": {"path": str(args.labels), "sha256": labels_hash},
        },
    }
    content = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        try:
            args.output.write_text(content)
        except OSError as exc:
            parser.error(f"cannot write output {args.output}: {exc}")
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
