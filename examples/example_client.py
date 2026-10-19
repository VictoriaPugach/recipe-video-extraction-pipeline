import argparse
import json
from pathlib import Path

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Пример REST-клиента Fropti")
    parser.add_argument("url", nargs="?", help="Ссылка на видео")
    parser.add_argument("--file", type=Path, help="Путь к локальному видео")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    if (args.url is None) == (args.file is None):
        parser.error("Укажи один источник: URL или --file")
    if args.file is not None and not args.file.is_file():
        parser.error(f"Файл не найден: {args.file}")
    try:
        if args.file is not None:
            with args.file.open("rb") as video:
                response = httpx.post(
                    f"{args.api.rstrip('/')}/extract/upload",
                    files={"file": (args.file.name, video, "application/octet-stream")},
                    timeout=180,
                )
        else:
            response = httpx.post(f"{args.api.rstrip('/')}/extract", json={"url": args.url}, timeout=180)
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
        response.raise_for_status()
    except (httpx.HTTPError, OSError, ValueError) as exc:
        print(f"Запрос не выполнен: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
