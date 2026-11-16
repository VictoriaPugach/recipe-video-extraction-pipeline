# Регрессии ревью: формат тела ошибки, JSON-ответ на непредвиденные сбои,
# история диалога при повторах LLM и классификация отсутствующего ffprobe.
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from fropti_core_contracts import UploadSource

from src import api, downloader, pipeline
from src.errors import MediaProcessingError
from src.llm import fusion

client = TestClient(api.app, raise_server_exceptions=False)


@pytest.mark.parametrize("content, status, code", [(b"", 400, "empty_file")])
def test_error_body_uses_message_key(content, status, code):
    response = client.post("/extract/upload", files={"file": ("v.mp4", content, "video/mp4")})
    detail = response.json()["detail"]
    assert response.status_code == status
    assert detail["code"] == code
    assert set(detail) == {"code", "message"}  # ключ message, как в документации API


def test_app_error_body_uses_message_key(monkeypatch):
    def fail(source):
        raise pipeline.VideoTooLongError("слишком длинное")

    monkeypatch.setattr(pipeline, "extract_recipe", fail)
    response = client.post("/extract", json={"url": "https://www.youtube.com/watch?v=abc"})
    assert response.status_code == 400
    assert response.json()["detail"] == {"code": "video_too_long", "message": "слишком длинное"}


def test_unexpected_exception_returns_json_internal_error(monkeypatch):
    def boom(source):
        raise ValueError("secret internal detail")

    monkeypatch.setattr(pipeline, "extract_recipe", boom)
    response = client.post("/extract", json={"url": "https://www.youtube.com/watch?v=abc"})
    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/json")
    detail = response.json()["detail"]
    assert detail["code"] == "internal_error"
    assert "secret internal detail" not in json.dumps(detail)  # подробности только в логах


def test_llm_retry_sends_back_the_models_own_answer(monkeypatch):
    attempts = []

    def fake_llm(messages):
        attempts.append([dict(m) for m in messages])
        return '{"title": "first-answer-marker"}' if len(attempts) == 1 else "not json"

    monkeypatch.setattr(fusion, "_call_llm", fake_llm)
    with pytest.raises(fusion.LLMInvalidResponseError):
        fusion.fuse_recipe("текст", "текст", UploadSource(type="upload"))
    second = attempts[1]
    assert [m["role"] for m in second][-2:] == ["assistant", "user"]
    assert "first-answer-marker" in second[-2]["content"]
    # Каждая следующая попытка видит всю историю, а не только последнюю обратную связь.
    assert [m["role"] for m in attempts[2]].count("assistant") == 2


def test_missing_ffprobe_is_a_server_error_not_unreadable_video(monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(MediaProcessingError) as info:
        downloader.probe_duration(Path("/tmp/does-not-matter.mp4"))
    assert info.value.http_status == 500
    assert info.value.code == "media_processing_failed"


def test_missing_yt_dlp_binary_is_server_error_not_download_failure(monkeypatch):
    # Отсутствующий бинарь yt-dlp — проблема окружения сервера, как и
    # отсутствие ffprobe: 500 downloader_unavailable, а не 502 download_failed.
    def no_binary(command, **kwargs):
        raise FileNotFoundError(f"[Errno 2] No such file or directory: {command[0]!r}")

    monkeypatch.setattr(downloader.subprocess, "run", no_binary)
    with pytest.raises(downloader.DownloaderUnavailableError) as exc_info:
        downloader.probe_duration("https://www.youtube.com/watch?v=abc")
    assert exc_info.value.http_status == 500
    assert exc_info.value.code == "downloader_unavailable"


def test_upload_rejects_oversized_content_length_before_body():
    # Честный клиент с большим Content-Length получал 413 только после полной
    # выгрузки тела (UploadFile.size заполняется при разборе multipart).
    # Middleware отклоняет запрос до чтения тела; порог берётся с запасом на
    # заголовки multipart, точную границу по файлу проверяет file.size,
    # chunked-запросы без Content-Length — тоже она.
    limit = api.MAX_UPLOAD_BYTES + api._UPLOAD_REJECT_MARGIN_BYTES + 1
    limit_mib = api.MAX_UPLOAD_BYTES / (1024 * 1024)
    response = client.post(
        "/extract/upload",
        content=b"tiny",
        headers={"Content-Length": str(limit)},
    )
    assert response.status_code == 413
    detail = response.json()["detail"]
    assert detail["code"] == "file_too_large"
    assert f"{limit_mib:g}" in detail["message"]
