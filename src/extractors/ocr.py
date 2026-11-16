# OCR надписей с кадров через PaddleOCR. Строки дедуплицируются глобально
# с сохранением порядка первого появления: статичная надпись видна на многих
# кадрах, но в выдачу попадает один раз. Цена: одинаковые короткие строки
# разных ингредиентов (два «1 tsp» над двумя банками) схлопываются в одну.
# Строки с уверенностью ниже OCR_MIN_CONFIDENCE отбрасываются до дедупликации.
import os
import threading
from pathlib import Path

from src.config import OCR_ENABLE_MKLDNN, OCR_LANG, OCR_MAX_CPU_ISA, OCR_MIN_CONFIDENCE
from src.errors import ModelUnavailableError, OcrError

_READER = None
# Ленивая загрузка под блокировкой: sync-обработчики FastAPI идут в threadpool.
_READER_LOCK = threading.Lock()
# Paddle-предикторы не потокобезопасны: вызовы .ocr() общего reader'а
# сериализуются (_READER_LOCK защищает только создание reader'а).
_INFER_LOCK = threading.Lock()


def _wrap_create_predictor_without_onednn(module) -> None:
    """Каждый предсказатель paddle получает явный disable_mkldnn().

    Paddle 3.x включает oneDNN по умолчанию; PaddleOCR 2.x при
    enable_mkldnn=False никогда не вызывает disable_mkldnn() — параметр
    молча игнорируется. На части CPU и в эмуляции oneDNN-джит падает
    с «could not create a primitive descriptor for a reorder primitive».
    """
    original = module.create_predictor
    if getattr(original, "_disables_onednn", False):
        return  # уже обёрнут — второй раз не оборачиваем

    def create_predictor_without_onednn(config, *args, **kwargs):
        config.disable_mkldnn()
        return original(config, *args, **kwargs)

    create_predictor_without_onednn._disables_onednn = True
    module.create_predictor = create_predictor_without_onednn


def _get_reader():
    global _READER
    if _READER is None:  # быстрый путь без захвата блокировки
        with _READER_LOCK:
            if _READER is None:
                # oneDNN читает ограничение ISA при инициализации — переменные задаются
                # до импорта paddleocr.
                os.environ.setdefault("ONEDNN_MAX_CPU_ISA", OCR_MAX_CPU_ISA)
                os.environ.setdefault("DNNL_MAX_CPU_ISA", OCR_MAX_CPU_ISA)  # старое имя той же переменной
                try:
                    from paddleocr import PaddleOCR
                except ImportError as exc:
                    raise ModelUnavailableError("OCR-модель не установлена: pip install paddleocr paddlepaddle") from exc
                if not OCR_ENABLE_MKLDNN:
                    try:
                        import paddle.inference as paddle_inference
                    except ImportError:  # paddle нет — ошибка выйдет ниже при создании PaddleOCR
                        pass
                    else:
                        _wrap_create_predictor_without_onednn(paddle_inference)
                try:
                    _READER = PaddleOCR(
                        use_angle_cls=True,
                        lang=OCR_LANG,
                        show_log=False,
                        enable_mkldnn=OCR_ENABLE_MKLDNN,  # выключается явно, см. _wrap_create_predictor_without_onednn
                    )
                except Exception as exc:
                    raise ModelUnavailableError(f"Не удалось инициализировать PaddleOCR: {exc}") from exc
    return _READER


def recognize_text(frame_paths: list[Path]) -> str:
    if not frame_paths:
        return ""
    reader = _get_reader()
    lines: list[str] = []
    seen: set[str] = set()
    for frame_path in frame_paths:
        try:
            with _INFER_LOCK:  # предиктор не переносит конкурентные вызовы
                pages = reader.ocr(str(frame_path), cls=True)
        except Exception as exc:
            raise OcrError(f"OCR не обработал кадр {frame_path.name}: {exc}") from exc
        for page in pages or []:
            for entry in page or []:
                text = str(entry[1][0]).strip()
                # Пара (текст, уверенность); без оценки трактуем как «доверяем».
                confidence = float(entry[1][1]) if len(entry[1]) > 1 else 1.0
                if confidence < OCR_MIN_CONFIDENCE:
                    continue  # мусорное прочтение: и в выдачу, и в seen не попадает
                key = " ".join(text.lower().split())
                if len(key) >= 2 and key not in seen:
                    seen.add(key)
                    lines.append(text)
    return "\n".join(lines)
