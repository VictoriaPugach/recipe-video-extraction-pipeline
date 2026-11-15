from __future__ import annotations

import math
import os
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

from fropti_core_contracts import UploadSource, UrlSource

from src import downloader
from src.config import DEBUG_ARTIFACTS_DIR, MAX_VIDEO_SECONDS
from src.errors import AppError, VideoUnreadableError
from src.extractors import frames as frames_mod
from src.extractors import ocr as ocr_mod
from src.extractors import stt as stt_mod
from src.llm import fusion
from src.models import Recipe


class VideoTooLongError(AppError):
    code = "video_too_long"
    http_status = 400


def _save_debug_artifacts(transcript: str, screen_text: str) -> None:
    # Опциональный выгруз промежуточных фактов запуска: без
    # DEBUG_ARTIFACTS_DIR записи нет; сбой записи (права, место) не ломает
    # извлечение — артефакты вспомогательные. Суффикс из pid и случайной
    # строки разводит параллельные запуски по разным подкаталогам.
    if not DEBUG_ARTIFACTS_DIR:
        return
    label = f"run-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    try:
        run_dir = Path(DEBUG_ARTIFACTS_DIR) / label
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "transcript.txt").write_text(transcript, encoding="utf-8")
        (run_dir / "screen_text.txt").write_text(screen_text, encoding="utf-8")
    except OSError:
        pass


def extract_recipe(source: str | Path) -> Recipe:
    # 1. Длительность проверяется до любой тяжёлой обработки.
    duration = downloader.probe_duration(source)
    if not math.isfinite(duration) or duration <= 0:
        raise VideoUnreadableError(f"Не удалось определить корректную длительность видео: {duration}")
    if duration > MAX_VIDEO_SECONDS:
        raise VideoTooLongError(
            f"Видео должно быть не длиннее {MAX_VIDEO_SECONDS} секунд; получено {duration:.1f}"
        )
    # 2. Локальный файл обрабатываем на месте; URL скачиваем во временную папку.
    with TemporaryDirectory(prefix="fropti-pipeline-") as directory:
        work_dir = Path(directory)
        if isinstance(source, Path):
            video_path = source
            recipe_source = UploadSource(type="upload")
        else:
            video_path = downloader.download_video(source, work_dir)
            recipe_source = UrlSource(type="url", url=source)
        # 3. Факты: речь (STT) и текст с экрана (кадры -> OCR).
        transcript = stt_mod.transcribe(video_path)
        frame_paths = frames_mod.extract_frames(video_path, work_dir / "frames")
        screen_text = ocr_mod.recognize_text(frame_paths)
        # 4. Артефакты пишутся до LLM: сбой fusion не теряет факты.
        _save_debug_artifacts(transcript, screen_text)
        # 5. LLM собирает Recipe; source берётся из реального входа, а не из LLM.
        return fusion.fuse_recipe(transcript, screen_text, recipe_source)
