import argparse
import json

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description="Пример REST-клиента Fropti")
    parser.add_argument("url", help="Ссылка на видео")
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    try:
        response = httpx.post(f"{args.api.rstrip('/')}/extract", json={"url": args.url}, timeout=180)
        print(json.dumps(response.json(), ensure_ascii=False, indent=2))
        response.raise_for_status()
    except (httpx.HTTPError, ValueError) as exc:
        print(f"Запрос не выполнен: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
