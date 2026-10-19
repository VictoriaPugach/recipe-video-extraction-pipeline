from pathlib import Path


def probe_duration(url: str) -> float:
    # TODO: Implement this — получить длительность в секундах до тяжёлой обработки.
    raise NotImplementedError("Получение метаданных видео ещё не реализовано")


def download_video(url: str, output_dir: Path) -> Path:
    # TODO: Implement this — скачать видео с таймаутом и ограничением размера.
    raise NotImplementedError("Скачивание видео ещё не реализовано")
