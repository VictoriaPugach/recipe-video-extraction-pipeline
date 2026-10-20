from copy import deepcopy
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys

from fropti_core_contracts import CONTRACT_VERSION
from fropti_core_contracts.models import (
    Ingredient,
    ManualSource,
    Quantity,
    Recipe,
    RecipeStep,
    UploadSource,
    UrlSource,
)
from fropti_core_contracts.schema import recipe_schema
from fropti_core_contracts.validation import validate_recipe, validate_recipe_json
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError
import pytest

from src.models import Recipe as ApiRecipe

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "packages/fropti-core-contracts/schemas/recipe-1.0.0.schema.json"
INVALID_VALUES = [
    (("schema_version",), "2.0.0"),
    (("title",), ""),
    (("title",), " \t\n"),
    (("title",), 123),
    (("servings",), 0),
    (("servings",), -1),
    (("servings",), 1.5),
    (("servings",), True),
    (("servings",), "2"),
    (("ingredients",), []),
    (("ingredients", 0, "name"), " "),
    (("ingredients", 0, "name"), 123),
    (("ingredients", 0, "note"), ""),
    (("ingredients", 0, "note"), " \n"),
    (("ingredients", 0, "quantity", "value"), 0),
    (("ingredients", 0, "quantity", "value"), -1),
    (("ingredients", 0, "quantity", "value"), True),
    (("ingredients", 0, "quantity", "value"), "200"),
    (("ingredients", 0, "quantity", "value"), None),
    (("ingredients", 0, "quantity", "unit"), "г"),
    (("ingredients", 0, "quantity", "unit"), "cup"),
    (("ingredients", 0, "quantity", "unit"), None),
    (("steps",), []),
    (("steps", 0), "Отварить пасту."),
    (("steps", 0, "instruction"), " \t"),
    (("steps", 0, "instruction"), 123),
    (("steps", 0, "duration_seconds"), 0),
    (("steps", 0, "duration_seconds"), 1.5),
    (("steps", 0, "duration_seconds"), True),
    (("steps", 0, "duration_seconds"), "60"),
    (("total_time_minutes",), 0),
    (("total_time_minutes",), 1.5),
    (("total_time_minutes",), True),
    (("total_time_minutes",), "20"),
    (("source",), None),
    (("source",), {"type": "other"}),
    (("source",), {"type": "url", "url": "not-a-url"}),
    (("source",), {"type": "url", "url": "ftp://example.org/video.mp4"}),
    (("source",), {"type": "upload", "path": "/tmp/private-video.mp4"}),
    (("source",), {"type": "manual", "url": "https://example.org/video"}),
    (("source_url",), "https://example.org/video"),
    (("id",), "invented-id"),
    (("ingredients", 0, "extra"), "unknown"),
    (("ingredients", 0, "quantity", "extra"), "unknown"),
    (("steps", 0, "extra"), "unknown"),
    (("source", "extra"), "unknown"),
]
REQUIRED_FIELDS = [
    ("schema_version",),
    ("title",),
    ("servings",),
    ("ingredients",),
    ("steps",),
    ("source",),
    ("ingredients", 0, "name"),
    ("ingredients", 0, "quantity"),
    ("ingredients", 0, "quantity", "value"),
    ("ingredients", 0, "quantity", "unit"),
    ("steps", 0, "instruction"),
    ("source", "type"),
    ("source", "url"),
]


@pytest.fixture
def recipe_data():
    return json.loads((ROOT / "data/expected_outputs/pasta.json").read_text())


@pytest.fixture
def schema_validator():
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text()), format_checker=FormatChecker())


def test_service_and_package_use_one_versioned_contract():
    assert ApiRecipe is Recipe
    assert CONTRACT_VERSION == "1.0.0"
    assert version("fropti-core-contracts") == CONTRACT_VERSION


def test_committed_schema_matches_public_model():
    schema = json.loads(SCHEMA_PATH.read_text())
    Draft202012Validator.check_schema(schema)
    assert schema == recipe_schema()
    assert schema["$id"] == f"urn:fropti:recipe:{CONTRACT_VERSION}"
    assert {key: value for key, value in schema.items() if key not in {"$schema", "$id"}} == Recipe.model_json_schema()


@pytest.mark.parametrize("name", ["pasta", "omelette", "salad"])
def test_examples_validate_in_python_and_json_schema(name, schema_validator):
    data = json.loads((ROOT / f"data/expected_outputs/{name}.json").read_text())
    schema_validator.validate(data)
    recipe = Recipe.model_validate(data)
    schema_validator.validate(recipe.model_dump(mode="json"))
    assert Recipe.model_validate_json(recipe.model_dump_json()) == recipe


@pytest.mark.parametrize(("source", "model"), [
    ({"type": "url", "url": "https://example.org/video"}, UrlSource),
    ({"type": "upload"}, UploadSource),
    ({"type": "manual"}, ManualSource),
])
def test_source_types_and_unknown_values_roundtrip(recipe_data, schema_validator, source, model):
    recipe_data["source"] = source
    recipe_data["servings"] = None
    recipe_data["ingredients"][0]["quantity"] = None
    recipe_data["ingredients"][0]["note"] = "Количество не указано в видео"
    recipe_data["steps"][0]["duration_seconds"] = None
    schema_validator.validate(recipe_data)
    recipe = Recipe.model_validate(recipe_data)
    assert isinstance(recipe.source, model)
    assert isinstance(recipe.ingredients[0], Ingredient)
    assert isinstance(recipe.steps[0], RecipeStep)
    assert recipe.servings is None
    assert recipe.ingredients[0].quantity is None
    assert recipe.total_time_minutes is None
    assert recipe.steps[0].duration_seconds is None
    restored = Recipe.model_validate_json(recipe.model_dump_json())
    assert restored == recipe
    schema_validator.validate(restored.model_dump(mode="json"))


@pytest.mark.parametrize("unit", ["g", "kg", "ml", "l", "pcs", "tsp", "tbsp", "pinch"])
def test_canonical_units_and_explicit_times(recipe_data, schema_validator, unit):
    recipe_data["ingredients"][0]["quantity"] = {"value": 0.5, "unit": unit}
    recipe_data["steps"][0]["duration_seconds"] = 60
    recipe_data["total_time_minutes"] = 20
    schema_validator.validate(recipe_data)
    recipe = Recipe.model_validate(recipe_data)
    assert isinstance(recipe.ingredients[0].quantity, Quantity)
    assert recipe.ingredients[0].quantity.value == 0.5
    assert recipe.ingredients[0].quantity.unit == unit
    assert recipe.steps[0].duration_seconds == 60
    assert recipe.total_time_minutes == 20


@pytest.mark.parametrize(("path", "value"), INVALID_VALUES)
def test_invalid_wire_values_rejected_by_both_validators(recipe_data, schema_validator, path, value):
    target = recipe_data
    for part in path[:-1]:
        target = target[part]
    target[path[-1]] = deepcopy(value)
    with pytest.raises(ValidationError):
        Recipe.model_validate_json(json.dumps(recipe_data))
    assert not schema_validator.is_valid(recipe_data)


@pytest.mark.parametrize("path", REQUIRED_FIELDS)
def test_missing_fields_rejected_by_both_validators(recipe_data, schema_validator, path):
    target = recipe_data
    for part in path[:-1]:
        target = target[part]
    del target[path[-1]]
    with pytest.raises(ValidationError):
        Recipe.model_validate_json(json.dumps(recipe_data))
    assert not schema_validator.is_valid(recipe_data)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_python_quantities_are_rejected(value):
    with pytest.raises(ValidationError):
        Quantity(value=value, unit="g")


def test_legacy_shape_is_rejected(recipe_data, schema_validator):
    del recipe_data["schema_version"]
    recipe_data["source_url"] = recipe_data.pop("source")["url"]
    recipe_data["steps"] = [step["instruction"] for step in recipe_data["steps"]]
    recipe_data["ingredients"] = [{"name": "Паста", "amount": 200, "unit": "г"}]
    with pytest.raises(ValidationError):
        Recipe.model_validate(recipe_data)
    assert not schema_validator.is_valid(recipe_data)


def test_whole_json_numbers_normalize_to_integers(recipe_data, schema_validator):
    recipe_data["servings"] = 2.0
    recipe_data["steps"][0]["duration_seconds"] = 60.0
    recipe_data["total_time_minutes"] = 20.0
    schema_validator.validate(recipe_data)
    recipe = Recipe.model_validate_json(json.dumps(recipe_data))
    assert type(recipe.servings) is int
    assert recipe.servings == 2
    assert type(recipe.steps[0].duration_seconds) is int
    assert recipe.steps[0].duration_seconds == 60
    assert type(recipe.total_time_minutes) is int
    assert recipe.total_time_minutes == 20


@pytest.mark.parametrize("consumer", ["video extraction", "cooking planner", "shopping list"])
def test_all_three_modules_accept_the_same_recipe(recipe_data, schema_validator, consumer):
    producer_result = validate_recipe(recipe_data)
    wire_payload = producer_result.model_dump_json()

    schema_validator.validate(json.loads(wire_payload))
    consumer_input = validate_recipe_json(wire_payload)

    assert consumer_input == producer_result, consumer
    assert consumer_input.steps, consumer
    assert consumer_input.ingredients, consumer


def test_contract_validator_cli_accepts_examples_and_rejects_invalid_json(tmp_path):
    valid_path = ROOT / "data/expected_outputs/pasta.json"
    invalid_path = tmp_path / "invalid.json"
    invalid_path.write_text('{"schema_version":"1.0.0"}', encoding="utf-8")

    valid = subprocess.run(
        [sys.executable, "-m", "fropti_core_contracts", str(valid_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    invalid = subprocess.run(
        [sys.executable, "-m", "fropti_core_contracts", str(invalid_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert valid.returncode == 0
    assert valid.stdout.startswith("OK ")
    assert valid.stderr == ""
    assert invalid.returncode == 1
    assert invalid.stdout.startswith("INVALID ")
    assert invalid.stderr == ""
