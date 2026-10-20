import argparse
from pathlib import Path

from pydantic import ValidationError

from fropti_core_contracts.validation import validate_recipe_json


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate JSON files against Recipe 1.0.0")
    parser.add_argument("files", type=Path, nargs="+")
    args = parser.parse_args()
    failed = False
    for path in args.files:
        try:
            validate_recipe_json(path.read_bytes())
        except OSError as error:
            failed = True
            print(f"ERROR {path}: {error}")
        except ValidationError as error:
            failed = True
            print(f"INVALID {path}: {error.error_count()} validation error(s)")
        else:
            print(f"OK {path}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
