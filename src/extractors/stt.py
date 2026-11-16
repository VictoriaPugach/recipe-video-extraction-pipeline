# STT: ffmpeg извлекает моно-PCM 16 кГц, faster-whisper распознаёт речь.
# Видео без аудиодорожки — не ошибка: расшифровка пустая, рецепт собирается по OCR.
import threading
from pathlib import Path
from tempfile import TemporaryDirectory

from src import media
from src.config import WHISPER_COMPUTE_TYPE, WHISPER_DEVICE, WHISPER_LANGUAGE, WHISPER_MODEL
from src.errors import MediaProcessingError, ModelUnavailableError

_MODEL = None
# FastAPI исполняет sync-обработчики в threadpool: ленивая загрузка —
# под блокировкой, иначе модель создаётся по разу на каждый первый запрос.
_MODEL_LOCK = threading.Lock()


def _get_model():
    global _MODEL
    if _MODEL is None:  # быстрый путь без захвата блокировки
        with _MODEL_LOCK:
            if _MODEL is None:
                try:
                    from faster_whisper import WhisperModel
                except ImportError as exc:
                    raise ModelUnavailableError("STT-модель не установлена: pip install faster-whisper") from exc
                try:
                    _MODEL = WhisperModel(WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE_TYPE)
                except Exception as exc:
                    # Сбой загрузки — проблема окружения, а не входа:
                    # 503 model_unavailable, как у OCR.
                    raise ModelUnavailableError(
                        f"Не удалось загрузить STT-модель {WHISPER_MODEL!r}: {exc}"
                    ) from exc
    return _MODEL


def transcribe(video_path: Path) -> str:
    # 1. Аудиодорожку проверяем до загрузки модели: видео без звука не нуждается в STT.
    try:
        has_audio = media.ffprobe_has_audio(video_path)
    except RuntimeError as exc:
        raise MediaProcessingError(str(exc)) from exc
    if not has_audio:
        return ""
    # 2. Модель загружается только когда есть что распознавать.
    model = _get_model()
    with TemporaryDirectory(prefix="fropti-audio-") as directory:
        wav_path = Path(directory) / "audio.wav"
        try:
            # check=True: сбой ffmpeg не проходит молча.
            media.run_media([
                "ffmpeg", "-y", "-i", str(video_path),
                "-vn", "-ac", "1", "-ar", "16000", str(wav_path),
            ])
        except RuntimeError as exc:
            raise MediaProcessingError(f"Не удалось извлечь аудиодорожку: {exc}") from exc
        # "auto" (или пусто) в faster-whisper — language=None; строка "auto"
        # как есть упала бы на валидации языка. OUTPUT_LANGUAGE — отдельная
        # настройка: язык ответа LLM, не входного аудио.
        language = (WHISPER_LANGUAGE or "").strip()
        segments, _info = model.transcribe(
            str(wav_path),
            language=None if language.lower() in ("", "auto") else language,
            vad_filter=True,
        )
        return " ".join(segment.text.strip() for segment in segments).strip()
