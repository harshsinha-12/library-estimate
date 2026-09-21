import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parents[2]
SCHEMAS = ROOT / "schemas"
FIXTURES = ROOT / "fixtures" / "stage1"


def load_json(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def registry() -> Registry:
    result = Registry()
    for path in SCHEMAS.glob("*.schema.json"):
        schema = load_json(path)
        result = result.with_resource(schema["$id"], Resource.from_contents(schema))
    return result


def test_all_json_schemas_are_valid() -> None:
    for path in SCHEMAS.glob("*.schema.json"):
        Draft202012Validator.check_schema(load_json(path))


def test_capture_package_fixture_matches_contract() -> None:
    schema = load_json(SCHEMAS / "capture-package.schema.json")
    Draft202012Validator(schema, registry=registry()).validate(
        load_json(FIXTURES / "capture-package.json")
    )


def test_survey_ir_fixture_matches_contract() -> None:
    schema = load_json(SCHEMAS / "survey-ir.schema.json")
    Draft202012Validator(schema, registry=registry()).validate(
        load_json(FIXTURES / "survey-ir.json")
    )


def test_every_stage_one_schema_has_a_valid_fixture() -> None:
    schema_names = {
        "capture-package",
        "evidence-package",
        "model-assessment",
        "price-observation",
        "review-decision",
        "rl-transition",
        "survey-ir",
        "survey-geography",
    }
    schema_registry = registry()
    for name in schema_names:
        schema = load_json(SCHEMAS / f"{name}.schema.json")
        fixture = load_json(FIXTURES / f"{name}.json")
        Draft202012Validator(schema, registry=schema_registry).validate(fixture)
