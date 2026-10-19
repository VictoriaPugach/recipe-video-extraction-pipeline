import math
import re
from enum import Enum
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, HttpUrl, StrictInt, StrictStr

CONTRACT_VERSION = "1.0.0"
URL_PATTERN = r"^https?://[^/\s?#]+(?:[/?#][^\s]*)?$"


def _normalize_integer(value: Any) -> Any:
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    return value


def _validate_url(value: Any) -> Any:
    if isinstance(value, str) and re.fullmatch(URL_PATTERN, value) is None:
        raise ValueError("URL must use HTTP/HTTPS, include a host and contain no whitespace")
    return value


NonEmptyString = Annotated[StrictStr, Field(min_length=1, pattern=r"\S")]
PositiveInteger = Annotated[StrictInt, Field(gt=0), BeforeValidator(_normalize_integer)]
PositiveNumber = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Unit(str, Enum):
    G = "g"
    KG = "kg"
    ML = "ml"
    L = "l"
    PCS = "pcs"
    TSP = "tsp"
    TBSP = "tbsp"
    PINCH = "pinch"


class Quantity(ContractModel):
    value: PositiveNumber
    unit: Unit


class Ingredient(ContractModel):
    name: NonEmptyString
    quantity: Quantity | None
    note: NonEmptyString | None = None


class RecipeStep(ContractModel):
    instruction: NonEmptyString
    duration_seconds: PositiveInteger | None = None


class UrlSource(ContractModel):
    type: Literal["url"]
    url: Annotated[
        HttpUrl,
        BeforeValidator(_validate_url),
        Field(json_schema_extra={"pattern": URL_PATTERN}),
    ]


class UploadSource(ContractModel):
    type: Literal["upload"]


class ManualSource(ContractModel):
    type: Literal["manual"]


Source = Annotated[Union[UrlSource, UploadSource, ManualSource], Field(discriminator="type")]


class Recipe(ContractModel):
    schema_version: Literal["1.0.0"]
    title: NonEmptyString
    servings: PositiveInteger | None
    ingredients: Annotated[list[Ingredient], Field(min_length=1)]
    steps: Annotated[list[RecipeStep], Field(min_length=1)]
    source: Source
    total_time_minutes: PositiveInteger | None = None
