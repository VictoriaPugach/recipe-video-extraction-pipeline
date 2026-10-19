import argparse
import sys

from pydantic import HttpUrl, TypeAdapter, ValidationError

from src import pipeline


def main() -> int:
    parser = argparse.ArgumentParser(description="Fropti: извлечение рецепта из короткого видео")
    parser.add_argument("url", help="HTTP(S)-ссылка на видео длительностью до 180 секунд")
    args = parser.parse_args()
    try:
        url = TypeAdapter(HttpUrl).validate_python(args.url)
    except ValidationError:
        parser.error("нужна корректная HTTP(S)-ссылка")
    try:
        # TODO: Implement this — реализовать общий пайплайн в src/pipeline.py.
        recipe = pipeline.extract_recipe(str(url))
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
