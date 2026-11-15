import os

from dotenv import load_dotenv

load_dotenv()

# Ограничения входа.
MAX_VIDEO_SECONDS = int(os.getenv("MAX_VIDEO_SECONDS", "180"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))

# Загрузчик URL
DOWNLOAD_TIMEOUT_SECONDS = int(os.getenv("DOWNLOAD_TIMEOUT_SECONDS", "30"))
# Обязательный контролируемый выход для yt-dlp. В Compose это Squid во
# внутренней сети; пустое значение запрещает URL-запуск, а не включает прямой
# доступ. Вне Docker укажите эквивалентный фильтрующий HTTP(S)-прокси.
EGRESS_PROXY_URL = os.getenv("EGRESS_PROXY_URL", "http://egress-proxy:3128").strip()
# JS-рантайм для yt-dlp: challenge-скрипты плеера YouTube исполняются в
# JavaScript, yt-dlp принимает node >= 22 / deno >= 2.3 / bun >= 1.2.11
# (Debian Bookworm ставит node 18 — поэтому в Dockerfile node 22 из NodeSource).
# Пустое значение отключает флаг.
YTDLP_JS_RUNTIME = os.getenv("YTDLP_JS_RUNTIME", "node").strip()
# Cookies для YouTube (Netscape cookies.txt): на части IP и видео YouTube
# требует авторизацию («Sign in to confirm you're not a bot»), без них
# извлечение падает с download_failed. Файл равнозначен доступу к аккаунту:
# не коммитить, не логировать. Пустое значение — флаг не передаётся.
YTDLP_COOKIES_FILE = os.getenv("YTDLP_COOKIES_FILE", "").strip()
# Альтернатива — cookies из профиля браузера. Chrome/Edge на Windows шифруют
# cookie-хранилище (App-Bound Encryption) — там надёжнее firefox или экспорт
# в файл. Пустое значение — флаг не передаётся.
YTDLP_COOKIES_FROM_BROWSER = os.getenv("YTDLP_COOKIES_FROM_BROWSER", "").strip()
# Клиент плеера YouTube (--extractor-args "youtube:player_client=…"): смена
# клиента иногда снимает «No title found in player responses» даже без cookies.
# Клиенты mweb/web без PO-токена отбрасывают форматы — надёжнее tv,web_safari.
# Пустое значение — стандартный выбор yt-dlp.
YTDLP_PLAYER_CLIENT = os.getenv("YTDLP_PLAYER_CLIENT", "").strip()
# Произвольные extractor-args yt-dlp, разделитель «;» — каждый элемент идёт
# отдельным --extractor-args (PO-токен, другой клиент и т.п.):
# YTDLP_EXTRACTOR_ARGS=youtube:po_token=mweb.gvs+XXX;youtube:player_client=tv
# Пустое значение — ничего не передаётся. Если здесь и в YTDLP_PLAYER_CLIENT
# задан один ключ, побеждает значение отсюда (идёт последним).
YTDLP_EXTRACTOR_ARGS = os.getenv("YTDLP_EXTRACTOR_ARGS", "").strip()
MAX_DOWNLOAD_BYTES = int(os.getenv("MAX_DOWNLOAD_BYTES", str(MAX_UPLOAD_BYTES)))
ALLOWED_URL_HOSTS = tuple(
    host.strip().lower()
    for host in os.getenv(
        "ALLOWED_URL_HOSTS",
        "youtube.com,www.youtube.com,m.youtube.com,youtu.be,music.youtube.com",
    ).split(",")
    if host.strip()
)

# Медиаобработка (нужен системный ffmpeg)
FFMPEG_TIMEOUT_SECONDS = int(os.getenv("FFMPEG_TIMEOUT_SECONDS", "120"))
MAX_FRAMES = int(os.getenv("MAX_FRAMES", "12"))
FRAME_WIDTH = int(os.getenv("FRAME_WIDTH", "1280"))

# STT (faster-whisper, CPU int8 по умолчанию)
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "small")
# Язык речи для faster-whisper: код языка ускоряет и уточняет расшифровку,
# "auto" (или пусто) — автодетект по первым 30 секундам аудио. Язык ответа
# LLM задаёт OUTPUT_LANGUAGE — это отдельная настройка.
WHISPER_LANGUAGE = os.getenv("WHISPER_LANGUAGE", "ru")
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "cpu")
# Точность вычислений faster-whisper: int8 — быстрый вариант для CPU;
# float16 — на GPU (WHISPER_DEVICE=cuda); float32 — эталон, но медленнее.
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "int8")

# OCR (PaddleOCR)
OCR_LANG = os.getenv("OCR_LANG", "ru")
# На части CPU и в эмуляции (Rosetta, QEMU) oneDNN в Paddle 3.x падает при
# создании JIT-примитивов, поэтому по умолчанию mkldnn выключается для каждого
# предсказателя OCR (см. src/extractors/ocr.py), инструкции ограничены AVX2.
# Включить: OCR_ENABLE_MKLDNN=1.
OCR_ENABLE_MKLDNN = os.getenv("OCR_ENABLE_MKLDNN", "0") == "1"
OCR_MAX_CPU_ISA = os.getenv("OCR_MAX_CPU_ISA", "AVX2")
# Минимальная уверенность PaddleOCR для строки текста, 0..1; строки ниже
# порога отбрасываются до дедупликации. По умолчанию 0 — порог выключен;
# при шумном распознавании попробуйте 0.5–0.6 (зависит от языка и кадров).
OCR_MIN_CONFIDENCE = min(1.0, max(0.0, float(os.getenv("OCR_MIN_CONFIDENCE", "0"))))

# LLM (OpenAI-совместимый API)
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "")
LLM_MODEL = os.getenv("LLM_MODEL", "")
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "180"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))
# Повторы транзиторных сетевых сбоев вызова LLM: обрыв соединения, таймаут
# httpx, статусы шлюза 408/429/502/503/504. Не то же, что LLM_MAX_RETRIES
# (невалидный JSON/нарушение схемы Recipe); постоянные ошибки (401/403/400)
# не повторяются. 0 — сетевой сбой падает сразу. max() отсекает отрицательные
# значения из .env.
LLM_NETWORK_RETRIES = max(0, int(os.getenv("LLM_NETWORK_RETRIES", "1")))
# Пауза между сетевыми повторами, секунды: моментальный повтор после 502/429
# почти всегда попадает в тот же сбой или усугубляет rate limit.
LLM_RETRY_DELAY_SECONDS = max(0.0, float(os.getenv("LLM_RETRY_DELAY_SECONDS", "2")))
# Температура генерации: 0.0 — самый детерминированный ответ для извлечения данных.
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.0"))
# Язык рецепта на выходе LLM: "auto" — язык исходного видео (речи и надписей),
# иначе явное указание, например OUTPUT_LANGUAGE=English.
OUTPUT_LANGUAGE = os.getenv("OUTPUT_LANGUAGE", "auto").strip()

# Опциональный каталог промежуточных артефактов: каждый запуск пишет
# transcript.txt и screen_text.txt в свой подкаталог run-*. Нужно для
# приёмки (real-testing.md) и разбора расхождений с эталоном. Файлы могут
# содержать персональные данные из речи: в репозиторий — только обезличенные.
# Пусто = артефакты не сохраняются.
DEBUG_ARTIFACTS_DIR = os.getenv("DEBUG_ARTIFACTS_DIR", "").strip()
