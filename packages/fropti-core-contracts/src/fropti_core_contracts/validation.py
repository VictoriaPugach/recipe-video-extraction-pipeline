from typing import Any

from fropti_core_contracts.models import Recipe


def validate_recipe(value: Any) -> Recipe:
    """Validate a decoded value against the shared Recipe contract."""
    return Recipe.model_validate(value)


def validate_recipe_json(value: str | bytes | bytearray) -> Recipe:
    """Validate a JSON document against the shared Recipe contract."""
    return Recipe.model_validate_json(value)
