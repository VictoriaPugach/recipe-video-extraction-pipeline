import argparse
from pathlib import Path
import sys

from pydantic import HttpUrl, TypeAdapter, ValidationError

from src import pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Fropti: извлечение рецепта из короткого видео")
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("url", nargs="?", help="HTTP(S)-ссылка на видео длительностью до 180 секунд")
    inputs.add_argument("--file", type=Path, help="Путь к локальному видео")
    args = parser.parse_args()
    if args.file is not None:
        if not args.file.is_file():
            parser.error("видеофайл не найден")
        source = args.file
    else:
        try:
            source = str(TypeAdapter(HttpUrl).validate_python(args.url))
        except ValidationError:
            parser.error("нужна корректная HTTP(S)-ссылка")
    try:
        # TODO: Implement this — реализовать общий пайплайн в src/pipeline.py.
        recipe = pipeline.extract_recipe(source)
    except pipeline.VideoTooLongError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except NotImplementedError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(recipe.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
