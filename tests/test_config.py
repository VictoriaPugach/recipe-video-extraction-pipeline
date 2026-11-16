# Конфигурация: настройки лимитов и параметров через переменные окружения.
import importlib

import src.config as config


def test_tuning_settings_read_from_env(monkeypatch):
    # Лимиты и параметры настраиваются переменными окружения, а не константами.
    monkeypatch.setenv("MAX_VIDEO_SECONDS", "600")
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "2048")
    monkeypatch.setenv("WHISPER_COMPUTE_TYPE", "float16")
    monkeypatch.setenv("LLM_TEMPERATURE", "0.2")
    reloaded = importlib.reload(config)
    try:
        assert reloaded.MAX_VIDEO_SECONDS == 600
        assert reloaded.MAX_UPLOAD_BYTES == 2048
        assert reloaded.WHISPER_COMPUTE_TYPE == "float16"
        assert reloaded.LLM_TEMPERATURE == 0.2
    finally:
        monkeypatch.undo()
        importlib.reload(config)  # вернуть значения по умолчанию остальным тестам


def test_tuning_defaults_unchanged(monkeypatch):
    # Без переменных окружения значения прежние: 180 секунд, 100 MiB, int8, 0.0.
    monkeypatch.delenv("MAX_VIDEO_SECONDS", raising=False)
    monkeypatch.delenv("MAX_UPLOAD_BYTES", raising=False)
    monkeypatch.delenv("WHISPER_COMPUTE_TYPE", raising=False)
    monkeypatch.delenv("LLM_TEMPERATURE", raising=False)
    reloaded = importlib.reload(config)
    try:
        assert reloaded.MAX_VIDEO_SECONDS == 180
        assert reloaded.MAX_UPLOAD_BYTES == 100 * 1024 * 1024
        assert reloaded.WHISPER_COMPUTE_TYPE == "int8"
        assert reloaded.LLM_TEMPERATURE == 0.0
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_ytdlp_auth_settings_read_from_env(monkeypatch):
    # Cookies и клиент плеера настраиваются переменными окружения; по умолчанию
    # все пустые — флаги аутентификации yt-dlp не передаются.
    monkeypatch.delenv("YTDLP_COOKIES_FILE", raising=False)
    monkeypatch.delenv("YTDLP_COOKIES_FROM_BROWSER", raising=False)
    monkeypatch.delenv("YTDLP_PLAYER_CLIENT", raising=False)
    monkeypatch.delenv("YTDLP_EXTRACTOR_ARGS", raising=False)
    reloaded = importlib.reload(config)
    assert reloaded.YTDLP_COOKIES_FILE == ""
    assert reloaded.YTDLP_COOKIES_FROM_BROWSER == ""
    assert reloaded.YTDLP_PLAYER_CLIENT == ""
    assert reloaded.YTDLP_EXTRACTOR_ARGS == ""
    monkeypatch.undo()

    monkeypatch.setenv("YTDLP_COOKIES_FILE", "/run/secrets/cookies.txt")
    monkeypatch.setenv("YTDLP_COOKIES_FROM_BROWSER", "firefox")
    monkeypatch.setenv("YTDLP_PLAYER_CLIENT", "tv,web_safari")
    monkeypatch.setenv("YTDLP_EXTRACTOR_ARGS", "youtube:po_token=mweb.gvs+XXX")
    reloaded = importlib.reload(config)
    try:
        assert reloaded.YTDLP_COOKIES_FILE == "/run/secrets/cookies.txt"
        assert reloaded.YTDLP_COOKIES_FROM_BROWSER == "firefox"
        assert reloaded.YTDLP_PLAYER_CLIENT == "tv,web_safari"
        assert reloaded.YTDLP_EXTRACTOR_ARGS == "youtube:po_token=mweb.gvs+XXX"
    finally:
        monkeypatch.undo()
        importlib.reload(config)  # вернуть значения по умолчанию остальным тестам
