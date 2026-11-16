# Общие вызовы ffprobe/ffmpeg для загрузчика и экстракторов.
import json
import shutil
import subprocess
from pathlib import Path

from src.config import FFMPEG_TIMEOUT_SECONDS


class ToolNotFoundError(RuntimeError):
    """ffmpeg/ffprobe не установлен на сервере — не вина загруженного видео."""


def run_media(
    command: list[str],
    timeout: int = FFMPEG_TIMEOUT_SECONDS,
    check: bool = True,
) -> subprocess.CompletedProcess:
    if shutil.which(command[0]) is None:
        raise ToolNotFoundError(f"Исполняемый файл {command[0]!r} не найден; установите ffmpeg")
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"{command[0]!r} превысил лимит времени {timeout} с") from exc
    if check and result.returncode != 0:
        # Без проверки сбой ffmpeg проходит молча: вызывающий код падает позже
        # на несозданном файле с исключением вне контракта.
        lines = (result.stderr or result.stdout or "").strip().splitlines()
        tail = lines[-1] if lines else f"код возврата {result.returncode}"
        raise RuntimeError(f"{command[0]!r} завершился с кодом {result.returncode}: {tail[:200]}")
    return result


def ffprobe_duration(video_path: Path) -> float:
    result = run_media([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json", str(video_path),
    ], check=False)  # своё сообщение об ошибке с потоком stderr ffprobe
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe не смог прочитать файл: {result.stderr.strip()[:200]}")
    try:
        return float(json.loads(result.stdout)["format"]["duration"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("ffprobe не вернул длительность") from exc


def ffprobe_has_audio(video_path: Path) -> bool:
    # Есть ли у файла аудиодорожка. Видео без звука — не ошибка:
    # STT вернёт пустую расшифровку, а рецепт соберётся по тексту с экрана.
    result = run_media([
        "ffprobe", "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "json", str(video_path),
    ], check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe не смог прочитать файл: {result.stderr.strip()[:200]}")
    try:
        return bool(json.loads(result.stdout).get("streams"))
    except json.JSONDecodeError as exc:
        raise RuntimeError("ffprobe не вернул JSON") from exc
