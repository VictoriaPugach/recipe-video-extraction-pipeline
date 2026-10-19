from src.config import CONTRACT_MODE

if CONTRACT_MODE == "shared":
    from fropti_core_contracts.models import Recipe
else:
    from src.models.draft import DraftRecipe as Recipe

__all__ = ["Recipe"]
