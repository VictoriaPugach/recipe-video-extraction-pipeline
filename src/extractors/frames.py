# Равномерный выбор кадров: ffmpeg fps-фильтр даёт не больше MAX_FRAMES
# кадров за всё видео. Повторы текстов убирает OCR; кадры дедуплицировать не
# требуется, потому что LLM получает объединённый текст.
import math
from pathlib import Path

from src import media
from src.config import FRAME_WIDTH, MAX_FRAMES
from src.errors import MediaProcessingError


def extract_frames(video_path: Path, output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        duration = media.ffprobe_duration(video_path)
    except RuntimeError as exc:
        raise MediaProcessingError(str(exc)) from exc
    if not math.isfinite(duration) or duration <= 0:
        raise MediaProcessingError(f"Некорректная длительность видео: {duration}")
    fps = MAX_FRAMES / duration
    pattern = str(output_dir / "frame_%03d.jpg")
    try:
        # check=True: пустая выдача после сбоя ffmpeg не выглядит как успех.
        media.run_media([
            "ffmpeg", "-y", "-i", str(video_path),
            "-vf", f"fps={fps:.6f},scale='min({FRAME_WIDTH},iw)':-2",
            "-frames:v", str(MAX_FRAMES),
            "-q:v", "3", pattern,
        ])
    except RuntimeError as exc:
        raise MediaProcessingError(f"Не удалось извлечь кадры: {exc}") from exc
    frames = sorted(output_dir.glob("frame_*.jpg"))
    if not frames:
        raise MediaProcessingError("ffmpeg не вернул ни одного кадра")
    return frames
