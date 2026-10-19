import argparse
import json
from pathlib import Path

from fropti_core_contracts.models import CONTRACT_VERSION, Recipe


def recipe_schema() -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:fropti:recipe:{CONTRACT_VERSION}",
        **Recipe.model_json_schema(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or verify the Recipe JSON Schema")
    destination = parser.add_mutually_exclusive_group()
    destination.add_argument("--output", type=Path)
    destination.add_argument("--check", type=Path)
    args = parser.parse_args()
    generated = json.dumps(recipe_schema(), ensure_ascii=False, indent=2) + "\n"
    if args.check is not None:
        try:
            actual = args.check.read_text(encoding="utf-8")
        except OSError as error:
            parser.exit(1, f"Cannot read schema: {error}\n")
        if actual != generated:
            parser.exit(1, f"Schema is out of date: {args.check}\n")
        print(f"Schema is current: {args.check}")
    elif args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(generated, encoding="utf-8")
    else:
        print(generated, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
