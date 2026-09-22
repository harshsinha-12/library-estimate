"""Evaluate a saved report against independent physical-zone labels."""

from __future__ import annotations

import argparse
import json
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from backend.app.evaluation.labeled import ZoneLabels, evaluate_zone


def _read(path: Path, description: str) -> tuple[bytes, str]:
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read {description} {path}: {exc}") from exc
    return content, sha256(content).hexdigest()


def _load_report(content: bytes) -> dict:
    try:
        report = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"report is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(report, dict):
        raise ValueError("report root must be a JSON object")
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate a report against independent physical-zone labels."
    )
    parser.add_argument("report", type=Path, help="saved survey report JSON")
    parser.add_argument("labels", type=Path, help="independently recorded labels JSON")
    parser.add_argument("--output", type=Path, help="write metrics JSON instead of stdout")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    try:
        report_content, report_sha256 = _read(args.report, "report")
        labels_content, labels_sha256 = _read(args.labels, "labels")
        report = _load_report(report_content)
        labels = ZoneLabels.model_validate_json(labels_content)
        result = evaluate_zone(report, labels)
    except (ValidationError, ValueError) as exc:
        parser.error(str(exc))

    result["provenance"]["input_files"] = {
        "report": {"path": str(args.report), "sha256": report_sha256},
        "labels": {"path": str(args.labels), "sha256": labels_sha256},
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
