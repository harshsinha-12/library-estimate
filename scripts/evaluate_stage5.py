"""Evaluate a saved report against independent physical-zone labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.app.evaluation.labeled import ZoneLabels, evaluate_zone


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    parser.add_argument("labels", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    labels = ZoneLabels.model_validate_json(args.labels.read_text())
    result = evaluate_zone(report, labels)
    content = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(content)
    else:
        print(content, end="")


if __name__ == "__main__":
    main()
