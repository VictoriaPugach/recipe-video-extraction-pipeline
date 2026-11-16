# Безопасное получение видео: длительность до тяжёлой обработки и скачивание
# только для URL из разрешённого списка платформ. Защита от SSRF: разбор URL,
# allowlist хостов, таймауты, ограничение размера файла, временные файлы с
# очисткой на уровне пайплайна. Все обращения yt-dlp идут через обязательный
# egress-прокси; DNS и адреса назначения проверяет Squid, а не internal-only API.
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

from src import media
from src.config import (
    ALLOWED_URL_HOSTS,
    DOWNLOAD_TIMEOUT_SECONDS,
    EGRESS_PROXY_URL,
    MAX_DOWNLOAD_BYTES,
    MAX_VIDEO_SECONDS,
    YTDLP_COOKIES_FILE,
    YTDLP_COOKIES_FROM_BROWSER,
    YTDLP_EXTRACTOR_ARGS,
    YTDLP_JS_RUNTIME,
    YTDLP_PLAYER_CLIENT,
)
from src.errors import AppError, DownloaderUnavailableError, MediaProcessingError, VideoUnreadableError


class DownloadError(AppError):
    code = "download_failed"
    http_status = 502


class UnsupportedPlatformError(DownloadError):
    code = "unsupported_platform"
    http_status = 400


class EgressConfigurationError(AppError):
    code = "egress_proxy_required"
    http_status = 503


def _run(command: list[str], timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired as exc:
        raise DownloadError(f"Команда {command[0]!r} превысила лимит времени {timeout} с") from exc
    except FileNotFoundError as exc:
        # Отсутствующий бинарник — проблема окружения сервера, а не входа:
        # 500 downloader_unavailable, а не 502 download_failed. Симметрично
        # с ffprobe (ToolNotFoundError → media_processing_failed).
        raise DownloaderUnavailableError(
            f"Исполняемый файл {command[0]!r} не найден; установите yt-dlp"
        ) from exc
    except OSError as exc:
        raise DownloadError(f"Не удалось запустить {command[0]!r}: {exc}") from exc


@contextmanager
def _private_cookies_file():
    # yt-dlp перезаписывает cookiefile при каждом запуске: общий файл при
    # конкурентных запросах портился бы. Каждому запуску — личная копия;
    # исходник только читается («:ro» и docker secrets работают), копия
    # удаляется после запуска.
    if not YTDLP_COOKIES_FILE:
        yield None
        return
    handle = tempfile.NamedTemporaryFile(prefix="fropti-cookies-", suffix=".txt", delete=False)
    handle.close()
    try:
        # copyfile, а не copy2: права исходника (например «:ro») не
        # переносятся — копия должна быть доступна yt-dlp на запись.
        shutil.copyfile(YTDLP_COOKIES_FILE, handle.name)
        yield handle.name
    finally:
        Path(handle.name).unlink(missing_ok=True)


# Первая строка Netscape cookies.txt должна содержать «# Netscape HTTP Cookie
# File» или «# HTTP Cookie File», иначе yt-dlp падает с невнятным traceback.
_NETSCAPE_MAGIC = re.compile(r"#( Netscape)? HTTP Cookie File", re.IGNORECASE)


def _assert_cookies_readable() -> None:
    # Сломанный cookiefile роняет yt-dlp невнятным traceback'ом — проверяем
    # заранее: каталог вместо файла (пустое монтирование Docker), нечитаемый
    # файл, не-Netscape формат. Право записи не проверяется: исходник никто
    # не пишет (см. _private_cookies_file).
    if not YTDLP_COOKIES_FILE:
        return
    path = Path(YTDLP_COOKIES_FILE)
    if path.is_dir():
        raise DownloadError(
            f"По пути {YTDLP_COOKIES_FILE!r} — каталог, а не файл cookies. Так бывает, "
            "когда в compose-файле раскомментировано монтирование «./cookies.txt:/cookies.txt», "
            "но самого файла cookies.txt рядом с compose-файлом ещё нет: Docker вместо "
            "ошибки создаёт пустой каталог. Удалите этот каталог, экспортируйте cookies.txt "
            "(расширение «Get cookies.txt LOCALLY») рядом с compose-файлом и пересоздайте "
            "контейнер: docker compose up -d."
        )
    if not path.exists() or not os.access(YTDLP_COOKIES_FILE, os.R_OK):
        raise DownloadError(
            f"Файл cookies не найден или не читается: {YTDLP_COOKIES_FILE!r}. "
            "Переменная YTDLP_COOKIES_FILE должна указывать на путь ВНУТРИ контейнера: "
            "раскомментируйте монтирование cookies.txt в compose-файле или отключите переменную."
        )
    try:
        with open(YTDLP_COOKIES_FILE, "rb") as fh:
            first_line = fh.readline(256)
    except OSError:
        return  # редкий race (файл исчез между проверками) — пусть падает yt-dlp
    try:
        header = first_line.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DownloadError(
            f"Файл cookies не в UTF-8 (обычно это UTF-16 от сохранения в PowerShell): "
            f"{YTDLP_COOKIES_FILE!r}. Экспортируйте cookies.txt заново расширением "
            "«Get cookies.txt LOCALLY» — первая строка файла должна быть "
            "«# Netscape HTTP Cookie File»."
        ) from exc
    if not _NETSCAPE_MAGIC.search(header):
        raise DownloadError(
            f"Файл cookies не распознан как Netscape cookies.txt: {YTDLP_COOKIES_FILE!r}. "
            "Первая строка должна начинаться с «# Netscape HTTP Cookie File» — частые "
            "причины: файл пуст, создан вручную (New-Item) или экспортирован в JSON. "
            "Нужен экспорт расширением «Get cookies.txt LOCALLY» в Netscape-формате."
        )


def _hostname(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise UnsupportedPlatformError(f"Поддерживаются только HTTP/HTTPS-ссылки на видео: {url!r}")
    return parsed.hostname.lower()


def _assert_host_allowed(hostname: str) -> None:
    if hostname not in ALLOWED_URL_HOSTS:
        raise UnsupportedPlatformError(
            f"Платформа {hostname!r} не поддерживается; разрешены: {', '.join(ALLOWED_URL_HOSTS)}"
        )


def _stderr_excerpt(stderr: str, limit: int = 400) -> str:
    # WARNING печатаются до финального ERROR — показываем конец потока;
    # traceback расширяет лимит, хвост без последней строки бесполезен.
    text = stderr.strip()
    if "Traceback (most recent call last)" in text:
        limit = 3000
    if len(text) <= limit:
        return text
    return text[-limit:]


def _yt_dlp_command(*extra: str, cookies_file: str | None = None) -> list[str]:
    # yt-dlp исполняет challenge YouTube в JS-рантайме (см. YTDLP_JS_RUNTIME);
    # cookies и клиент плеера — против «Sign in to confirm you're not a bot»,
    # проверка срабатывает уже на метаданных (-J). cookies_file — личная копия
    # (_private_cookies_file): yt-dlp перезаписывает cookiefile при выходе.
    # Оба вызова (метаданные и скачивание) обязаны идти через контрольную
    # точку. Пустая настройка — отказ конфигурации, никогда не direct fallback.
    if not EGRESS_PROXY_URL:
        raise EgressConfigurationError(
            "Для URL-извлечения задайте EGRESS_PROXY_URL; прямой исходящий доступ запрещён"
        )
    command = ["yt-dlp", "--no-playlist", "--proxy", EGRESS_PROXY_URL]
    if YTDLP_JS_RUNTIME:
        command += ["--js-runtimes", YTDLP_JS_RUNTIME]
    if cookies_file:
        command += ["--cookies", cookies_file]
    if YTDLP_COOKIES_FROM_BROWSER:
        command += ["--cookies-from-browser", YTDLP_COOKIES_FROM_BROWSER]
    if YTDLP_PLAYER_CLIENT:
        command += ["--extractor-args", f"youtube:player_client={YTDLP_PLAYER_CLIENT}"]
    # Произвольные extractor-args (разделитель «;»): обходной путь для меняющихся
    # требований YouTube без правки кода, например PO-токен:
    # YTDLP_EXTRACTOR_ARGS=youtube:po_token=mweb.gvs+XXX
    if YTDLP_EXTRACTOR_ARGS:
        for piece in YTDLP_EXTRACTOR_ARGS.split(";"):
            piece = piece.strip()
            if piece:
                command += ["--extractor-args", piece]
    return command + list(extra)


def _yt_dlp_metadata(url: str) -> dict:
    with _private_cookies_file() as cookies:
        result = _run(
            _yt_dlp_command("--skip-download", "-J", url, cookies_file=cookies),
            DOWNLOAD_TIMEOUT_SECONDS,
        )
    if result.returncode != 0:
        raise DownloadError(f"yt-dlp не получил метаданные: {_stderr_excerpt(result.stderr)}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise DownloadError("yt-dlp вернул не-JSON ответ") from exc


def probe_duration(source: str | Path) -> float:
    # Длительность до STT/OCR: локальный файл — ffprobe, URL — метаданные
    # платформы (yt-dlp -J) без скачивания. Метаданные при скачивании не
    # переиспользуются — см. комментарий в download_video.
    if isinstance(source, Path):
        try:
            return media.ffprobe_duration(source)
        except media.ToolNotFoundError as exc:
            # Отсутствующий ffprobe — проблема окружения, а не файла: 5xx, а не 400.
            raise MediaProcessingError(str(exc)) from exc
        except RuntimeError as exc:
            raise VideoUnreadableError(str(exc)) from exc
    _assert_host_allowed(_hostname(source))
    _assert_cookies_readable()
    metadata = _yt_dlp_metadata(source)
    duration = metadata.get("duration")
    if duration is None:
        # Платформа ответила, но длительность не сообщила: лимит подтвердить
        # нельзя, сетевого сбоя не было — 4xx, а не 502.
        raise VideoUnreadableError(
            "Платформа не сообщила длительность видео — нельзя подтвердить, "
            f"что оно не длиннее {MAX_VIDEO_SECONDS} секунд"
        )
    try:
        return float(duration)
    except (TypeError, ValueError) as exc:
        # Мусор вместо числа не должен превращаться в необработанный ValueError.
        raise VideoUnreadableError(f"Платформа сообщила недостоверную длительность: {duration!r}") from exc


# Потолок 720p — предпочтительный, не жёсткий: PaddleOCR на 144–360p почти
# слеп, height-фильтр стоит не во всех ветках, 1080p лучше провала загрузки
# (--max-filesize ограничивает размер). bv*+ba — дефолтная пара yt-dlp,
# b-ветки нужны источникам без отдельных аудиодорожек; worst — запас.
_FORMAT_SELECTOR = (
    "bv*[height<=720][ext=mp4]+ba/bv*[height<=720]+ba"
    "/b[height<=720][ext=mp4]/b[height<=720]"
    "/bv*[ext=mp4]+ba/bv*+ba/b[ext=mp4]/b/worst"
)


def download_video(url: str, output_dir: Path) -> Path:
    # Второй вызов yt-dlp для той же страницы: лимит длительности обязан
    # срабатывать до скачивания, а --load-info-json ненадёжен — прямые
    # ссылки на медиа живут минуты. Повторное извлечение стоит секунды.
    _assert_host_allowed(_hostname(url))
    _assert_cookies_readable()
    output_dir.mkdir(parents=True, exist_ok=True)
    template = str(output_dir / "source.%(ext)s")
    with _private_cookies_file() as cookies:
        result = _run(
            _yt_dlp_command(
                "--restrict-filenames",
                "--max-filesize", str(MAX_DOWNLOAD_BYTES),
                "-f", _FORMAT_SELECTOR,
                "-o", template,
                url,
                cookies_file=cookies,
            ),
            DOWNLOAD_TIMEOUT_SECONDS * 4,
        )
    if result.returncode != 0:
        raise DownloadError(f"Не удалось скачать видео: {_stderr_excerpt(result.stderr)}")
    candidates = sorted(output_dir.glob("source.*"))
    if not candidates:
        raise DownloadError("После скачивания файл не появился")
    return candidates[0]
