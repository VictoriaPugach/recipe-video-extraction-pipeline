#!/usr/bin/env bash
# Загрузка локального видео в POST /extract/upload с проверкой файла ДО curl.
# Без проверки пропавший файл выглядит как «curl: (26) Failed to open/read
# local data from file/application» — curl не называет файл и не говорит,
# что запрос не дошёл до сервера. Здесь ошибка понятная: имя и путь.
#
# Использование:
#   scripts/upload.sh data/input_videos/tomatoChickpea.mp4 [host]
#   host по умолчанию http://localhost:8000
#
# Примеры:
#   scripts/upload.sh data/input_videos/tomatoChickpea.mp4
#   scripts/upload.sh /abs/path/video.mp4 http://127.0.0.1:8000

set -euo pipefail

if [[ $# -lt 1 || -z "${1:-}" ]]; then
  echo "usage: $0 <video.mp4> [host]   # host default: http://localhost:8000" >&2
  exit 2
fi

file=$1
host=${2:-http://localhost:8000}

if [[ ! -f "$file" ]]; then
  echo "error: file not found: $file" >&2
  echo "       запусти из корня репозитория или передай абсолютный путь;" >&2
  echo "       реальные MP4 в репозиторий не входят (см. docs/faq.md)" >&2
  exit 1
fi

exec curl -sS -X POST "$host/extract/upload" -F "file=@$file"
