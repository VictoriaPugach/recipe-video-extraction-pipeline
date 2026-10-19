from src import downloader
from src.config import MAX_VIDEO_SECONDS
from src.models import Recipe


class VideoTooLongError(ValueError):
    pass


def extract_recipe(url: str) -> Recipe:
    duration = downloader.probe_duration(url)
    if duration > MAX_VIDEO_SECONDS:
        raise VideoTooLongError(f"Видео должно быть не длиннее {MAX_VIDEO_SECONDS} секунд")
    # TODO: Implement this — скачать видео во временную директорию с автоматической очисткой.
    # TODO: Implement this — извлечь речь, кадры и экранный текст через src.extractors.
    # TODO: Implement this — вызвать src.llm.fusion и вернуть валидный Recipe.
    raise NotImplementedError("Извлечение рецепта ещё не реализовано")
