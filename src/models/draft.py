from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, HttpUrl

NonEmptyString = Annotated[str, Field(min_length=1)]


class Ingredient(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: NonEmptyString
    amount: Annotated[float, Field(gt=0)] | None = None
    unit: NonEmptyString | None = None


class DraftRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: NonEmptyString
    servings: Annotated[int, Field(gt=0, strict=True)]
    ingredients: Annotated[list[Ingredient], Field(min_length=1)]
    steps: Annotated[list[NonEmptyString], Field(min_length=1)]
    source_url: HttpUrl | None = None
