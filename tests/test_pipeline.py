import json
import os
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError

from src import downloader, pipeline
from src.api import app
from fropti_core_contracts import CONTRACT_VERSION
from src.models import Recipe

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(app)
VIDEO_URL = "https://example.org/videos/pasta"


@pytest.fixture
def recipe_data():
    return json.loads((ROOT / "data/expected_outputs/pasta.json").read_text())


@pytest.fixture
def fake_pipeline(monkeypatch, tmp_path, recipe_data):
    # Полный пайплайн подменяется, чтобы проверять границы длительности
    # и порядок вызовов без моделей, сети и ffmpeg.
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")

    def fake_download(url, output_dir):
        output_dir.mkdir(parents=True, exist_ok=True)
        return video

    monkeypatch.setattr(downloader, "download_video", fake_download)
    monkeypatch.setattr(pipeline.stt_mod, "transcribe", lambda path: "тестовая речь")
    monkeypatch.setattr(pipeline.frames_mod, "extract_frames", lambda path, out: [])
    monkeypatch.setattr(pipeline.ocr_mod, "recognize_text", lambda frames: "")
    monkeypatch.setattr(
        pipeline.fusion,
        "fuse_recipe",
        lambda transcript, screen_text, source: Recipe.model_validate(
            {**recipe_data, "source": source}
        ),
    )
    return recipe_data


@pytest.mark.parametrize("name", ["pasta", "omelette", "salad"])
def test_example_json_matches_shared_contract(name):
    raw = (ROOT / f"data/expected_outputs/{name}.json").read_text()
    recipe = Recipe.model_validate_json(raw)
    assert Recipe.model_validate_json(recipe.model_dump_json()) == recipe


@pytest.mark.parametrize(
    ("field", "value"),
    [("title", ""), ("servings", 0), ("ingredients", []), ("steps", []), ("source", {"type": "url", "url": "not-a-url"})],
)
def test_invalid_recipe_is_rejected(recipe_data, field, value):
    recipe_data[field] = value
    with pytest.raises(ValidationError):
        Recipe.model_validate(recipe_data)


def test_health_identifies_contract_version():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "contract_version": CONTRACT_VERSION}


def test_openapi_describes_json_body_and_recipe():
    response = client.get("/openapi.json")
    assert response.status_code == 200
    operation = response.json()["paths"]["/extract"]["post"]
    assert "application/json" in operation["requestBody"]["content"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith(Recipe.__name__)


@pytest.mark.parametrize("payload", [{}, {"url": "oops"}, {"url": "file:///tmp/video.mp4"}, {"url": VIDEO_URL, "extra": True}])
def test_extract_rejects_invalid_body(payload):
    assert client.post("/extract", json=payload).status_code == 422


def test_extract_requires_json_body():
    assert client.post("/extract", params={"url": VIDEO_URL}).status_code == 422


def test_extract_rejects_unsupported_platform(monkeypatch):
    # probe_duration подменён: платформа проверяется уже на скачивании.
    monkeypatch.setattr(downloader, "probe_duration", lambda url: 60.0)
    response = client.post("/extract", json={"url": VIDEO_URL})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsupported_platform"


@pytest.mark.parametrize(("duration", "status"), [(179.9, 200), (180, 200), (180.01, 400)])
def test_duration_boundary(monkeypatch, fake_pipeline, duration, status):
    monkeypatch.setattr(downloader, "probe_duration", lambda url: duration)
    response = client.post("/extract", json={"url": VIDEO_URL})
    assert response.status_code == status
    if status == 400:
        assert response.json()["detail"]["code"] == "video_too_long"


def test_extract_serializes_recipe_from_pipeline(monkeypatch, recipe_data):
    recipe = Recipe.model_validate(recipe_data)
    received_urls = []

    def extract(url):
        received_urls.append(url)
        return recipe

    monkeypatch.setattr(pipeline, "extract_recipe", extract)
    response = client.post("/extract", json={"url": VIDEO_URL})
    assert response.status_code == 200
    assert response.json() == recipe.model_dump(mode="json")
    assert received_urls == [VIDEO_URL]


@pytest.mark.parametrize("invalid_recipe", [{}, {"schema_version": "2.0.0"}])
def test_extract_does_not_return_invalid_recipe(monkeypatch, recipe_data, invalid_recipe):
    payload = {**recipe_data, **invalid_recipe} if invalid_recipe else {}
    monkeypatch.setattr(pipeline, "extract_recipe", lambda url: payload)
    with TestClient(app, raise_server_exceptions=False) as error_client:
        assert error_client.post("/extract", json={"url": VIDEO_URL}).status_code == 500


def test_pipeline_orchestrates_stages_for_url(monkeypatch, tmp_path):
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    calls = []
    monkeypatch.setattr(downloader, "probe_duration", lambda source: 120.0)
    monkeypatch.setattr(
        downloader,
        "download_video",
        lambda url, out_dir: calls.append(("download", url)) or video,
    )
    monkeypatch.setattr(pipeline.stt_mod, "transcribe", lambda path: calls.append(("stt", path)) or "речь")
    monkeypatch.setattr(
        pipeline.frames_mod,
        "extract_frames",
        lambda path, out: calls.append(("frames", path)) or [out / "f.jpg"],
    )
    monkeypatch.setattr(
        pipeline.ocr_mod,
        "recognize_text",
        lambda frames: calls.append(("ocr", frames)) or "текст с экрана",
    )

    def fake_fusion(transcript, screen_text, source):
        calls.append(("fusion", source.type))
        return Recipe.model_validate({
            "schema_version": "1.0.0",
            "title": "Тест",
            "servings": 1,
            "ingredients": [{"name": "Яйца", "quantity": {"value": 2, "unit": "pcs"}, "note": None}],
            "steps": [{"instruction": "Готовить.", "duration_seconds": None}],
            "source": source,
            "total_time_minutes": None,
        })

    monkeypatch.setattr(pipeline.fusion, "fuse_recipe", fake_fusion)
    recipe = pipeline.extract_recipe("https://youtu.be/abc123")
    assert [name for name, _ in calls] == ["download", "stt", "frames", "ocr", "fusion"]
    assert calls[0][1] == "https://youtu.be/abc123"
    assert calls[-1][1] == "url"
    assert recipe.source.type == "url"


def test_pipeline_uses_upload_source_for_local_file(monkeypatch, tmp_path):
    video = tmp_path / "local.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr(downloader, "probe_duration", lambda source: 30.0)
    monkeypatch.setattr(
        downloader,
        "download_video",
        lambda url, out_dir: pytest.fail("локальный файл не должен скачиваться"),
    )
    monkeypatch.setattr(pipeline.stt_mod, "transcribe", lambda path: "речь")
    monkeypatch.setattr(pipeline.frames_mod, "extract_frames", lambda path, out: [])
    monkeypatch.setattr(pipeline.ocr_mod, "recognize_text", lambda frames: "")
    seen_sources = []

    def fake_fusion(transcript, screen_text, source):
        seen_sources.append(source)
        return Recipe.model_validate({
            "schema_version": "1.0.0",
            "title": "Тест",
            "servings": None,
            "ingredients": [{"name": "Соль", "quantity": None, "note": "По вкусу"}],
            "steps": [{"instruction": "Готовить.", "duration_seconds": None}],
            "source": source,
            "total_time_minutes": None,
        })

    monkeypatch.setattr(pipeline.fusion, "fuse_recipe", fake_fusion)
    recipe = pipeline.extract_recipe(video)
    assert seen_sources[0].type == "upload"
    assert recipe.source.type == "upload"


def test_pipeline_rejects_empty_transcript_and_screen_text(monkeypatch, tmp_path):
    from src.errors import NoUsableContentError

    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    monkeypatch.setattr(downloader, "probe_duration", lambda source: 10.0)
    monkeypatch.setattr(pipeline.stt_mod, "transcribe", lambda path: "   ")
    monkeypatch.setattr(pipeline.frames_mod, "extract_frames", lambda path, out: [out / "f.jpg"])
    monkeypatch.setattr(pipeline.ocr_mod, "recognize_text", lambda frames: "")
    with pytest.raises(NoUsableContentError):
        pipeline.extract_recipe(video)


def test_pipeline_saves_debug_artifacts(monkeypatch, tmp_path):
    # DEBUG_ARTIFACTS_DIR: расшифровка речи и текст с экрана сохраняются до
    # fusion — артефакты приёмки (real-testing.md) доступны даже при сбое LLM.
    video = tmp_path / "video.mp4"
    video.write_bytes(b"video")
    debug_dir = tmp_path / "artifacts"
    monkeypatch.setattr(pipeline, "DEBUG_ARTIFACTS_DIR", str(debug_dir))
    monkeypatch.setattr(downloader, "probe_duration", lambda source: 10.0)
    monkeypatch.setattr(pipeline.stt_mod, "transcribe", lambda path: "речь из видео")
    monkeypatch.setattr(pipeline.frames_mod, "extract_frames", lambda path, out: [])
    monkeypatch.setattr(pipeline.ocr_mod, "recognize_text", lambda frames: "надпись")
    monkeypatch.setattr(
        pipeline.fusion,
        "fuse_recipe",
        lambda transcript, screen_text, source: Recipe.model_validate({
            "schema_version": "1.0.0",
            "title": "Тест",
            "servings": None,
            "ingredients": [{"name": "Соль", "quantity": None, "note": None}],
            "steps": [{"instruction": "Готовить.", "duration_seconds": None}],
            "source": source,
            "total_time_minutes": None,
        }),
    )
    pipeline.extract_recipe(video)
    runs = list(debug_dir.glob("run-*"))
    assert len(runs) == 1
    assert (runs[0] / "transcript.txt").read_text(encoding="utf-8") == "речь из видео"
    assert (runs[0] / "screen_text.txt").read_text(encoding="utf-8") == "надпись"


@pytest.mark.parametrize(("url", "code"), [(VIDEO_URL, 1), ("not-a-url", 2)])
def test_cli_reports_error_without_recipe(url, code):
    result = subprocess.run(
        [sys.executable, "-m", "src.main", url], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == code
    assert result.stdout == ""
    assert result.stderr
    assert "Traceback" not in result.stderr


def test_yt_dlp_command_default_has_no_auth_flags():
    # Базовый вызов: JS-рантайм передаётся; флагов аутентификации без
    # настроенных переменных быть не должно.
    command = downloader._yt_dlp_command("--skip-download", "-J", VIDEO_URL)
    assert command[:2] == ["yt-dlp", "--no-playlist"]
    assert "--js-runtimes" in command
    proxy_index = command.index("--proxy")
    assert command[proxy_index + 1] == "http://egress-proxy:3128"
    assert "--cookies" not in command
    assert "--cookies-from-browser" not in command
    assert "--extractor-args" not in command


def test_yt_dlp_command_fails_closed_without_egress_proxy(monkeypatch):
    # Пустая настройка не должна превращаться в прямой запуск yt-dlp.
    monkeypatch.setattr(downloader, "EGRESS_PROXY_URL", "")
    with pytest.raises(downloader.EgressConfigurationError) as exc_info:
        downloader._yt_dlp_command("-J", VIDEO_URL)
    assert exc_info.value.code == "egress_proxy_required"
    assert exc_info.value.http_status == 503


def test_yt_dlp_command_passes_cookies_file_and_player_client(monkeypatch):
    # Cookies-файл и клиент плеера попадают в оба вызова yt-dlp (метаданные и
    # скачивание используют один и тот же сборщик команды). cookies_file —
    # ЛИЧНАЯ копия из _private_cookies_file, а не общий YTDLP_COOKIES_FILE:
    # yt-dlp перезаписывает cookiefile при выходе, общая запись — гонка.
    monkeypatch.setattr(downloader, "YTDLP_COOKIES_FROM_BROWSER", "")
    monkeypatch.setattr(downloader, "YTDLP_PLAYER_CLIENT", "tv,web_safari")
    command = downloader._yt_dlp_command("-J", VIDEO_URL, cookies_file="/tmp/fropti-cookies-ab12cd34/cookies.txt")
    i = command.index("--cookies")
    assert command[i + 1] == "/tmp/fropti-cookies-ab12cd34/cookies.txt"
    j = command.index("--extractor-args")
    assert command[j + 1] == "youtube:player_client=tv,web_safari"
    assert "--cookies-from-browser" not in command


def test_yt_dlp_command_passes_cookies_from_browser(monkeypatch):
    monkeypatch.setattr(downloader, "YTDLP_COOKIES_FILE", "")
    monkeypatch.setattr(downloader, "YTDLP_COOKIES_FROM_BROWSER", "firefox")
    command = downloader._yt_dlp_command()
    i = command.index("--cookies-from-browser")
    assert command[i + 1] == "firefox"
    assert "--cookies" not in command


def test_yt_dlp_command_splits_extractor_args(monkeypatch):
    # YTDLP_EXTRACTOR_ARGS: каждый элемент «;» — отдельный --extractor-args.
    monkeypatch.setattr(downloader, "YTDLP_EXTRACTOR_ARGS", "youtube:po_token=mweb.gvs+XXX ; youtube:player_client=tv")
    command = downloader._yt_dlp_command()
    assert command.count("--extractor-args") == 2
    i = command.index("--extractor-args")
    assert command[i + 1] == "youtube:po_token=mweb.gvs+XXX"
    j = command.index("--extractor-args", i + 1)
    assert command[j + 1] == "youtube:player_client=tv"


def test_yt_dlp_metadata_error_shows_tail_of_stderr(monkeypatch):
    # WARNING-строки печатаются до финального ERROR; хвост stderr обязан
    # попадать в сообщение, а начало с предупреждениями — нет.
    stderr = "\n".join(f"WARNING: filler {i:02d} " + "y" * 32 for i in range(20))
    stderr += "\nERROR: Sign in to confirm you're not a bot"
    fake = subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=stderr)
    monkeypatch.setattr(downloader, "_run", lambda *a, **k: fake)
    with pytest.raises(downloader.DownloadError) as exc_info:
        downloader._yt_dlp_metadata(VIDEO_URL)
    assert "Sign in to confirm" in str(exc_info.value)
    assert "WARNING: filler 00" not in str(exc_info.value)


def test_format_selector_order():
    # Целевой случай — лучшее видео ≤720p с отдельным аудио (у YouTube
    # комбинированный файл обычно только 360p); затем комбинированные ветки
    # для источников без отдельных дорожек, затем любое качество, worst — последним.
    parts = downloader._FORMAT_SELECTOR.split("/")
    assert parts[0] == "bv*[height<=720][ext=mp4]+ba"
    assert parts[1] == "bv*[height<=720]+ba"
    assert parts[2].startswith("b[height<=720]")
    assert parts[4].startswith("bv*[ext=mp4]+ba")
    assert parts[-1] == "worst"


def test_stderr_excerpt_expands_limit_for_traceback():
    # Сырой Python-traceback (например, OOM-убийство процесса yt-dlp) обрывается
    # на середине кадра без строки-исключения; лимит 400 символов делал хвост
    # бесполезным для диагноза — для traceback лимит расширяется до 3000.
    frames = "\n".join(f'  File "/app/x{i}.py", line {i}, in f{i}\n    f{i + 1}()' for i in range(30))
    stderr = f"Traceback (most recent call last):\n{frames}\nOSError: [Errno 28] No space left on device"
    excerpt = downloader._stderr_excerpt(stderr)
    assert excerpt.endswith("OSError: [Errno 28] No space left on device")
    assert "Traceback (most recent call last)" in excerpt


def test_probe_duration_without_platform_duration_is_client_error(monkeypatch):
    # Метаданные без duration — 400 video_unreadable (нельзя подтвердить
    # лимит длительности), а не 502 download_failed: сетевого сбоя не было,
    # 502 остаётся для случая, когда метаданные получить не удалось.
    monkeypatch.setattr(downloader, "_yt_dlp_metadata", lambda url: {"id": "abc123"})
    with pytest.raises(downloader.VideoUnreadableError) as exc_info:
        downloader.probe_duration("https://youtu.be/abc123")
    assert exc_info.value.code == "video_unreadable"


def test_probe_duration_garbage_value_is_client_error(monkeypatch):
    # Мусор вместо числа длительности — 400 video_unreadable (данные
    # платформы недостоверны), а не необработанный ValueError.
    monkeypatch.setattr(downloader, "_yt_dlp_metadata", lambda url: {"duration": "abc"})
    with pytest.raises(downloader.VideoUnreadableError, match="недостоверную длительность"):
        downloader.probe_duration("https://youtu.be/abc123")


def test_missing_cookies_file_fails_fast(tmp_path):
    # yt-dlp при отсутствующем/нечитаемом cookiefile падает сырым
    # Python-traceback (yt_dlp/cookies.py) — файл проверяется до вызова
    # yt-dlp, ответ содержит понятную подсказку про монтирование.
    missing = tmp_path / "absent.txt"
    downloader.YTDLP_COOKIES_FILE = str(missing)
    try:
        with pytest.raises(downloader.DownloadError) as exc_info:
            downloader._assert_cookies_readable()
        assert "absent.txt" in str(exc_info.value)
        assert "ВНУТРИ контейнера" in str(exc_info.value)
    finally:
        downloader.YTDLP_COOKIES_FILE = ""

    downloader._assert_cookies_readable()  # пустая переменная — проверка не мешает

    good = tmp_path / "cookies.txt"
    good.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    downloader.YTDLP_COOKIES_FILE = str(good)
    try:
        downloader._assert_cookies_readable()  # читаемый файл — проверка проходит
    finally:
        downloader.YTDLP_COOKIES_FILE = ""


def test_broken_cookies_files_fail_fast(tmp_path):
    # Сломанный cookiefile ломает yt-dlp на разных этапах — проверяем
    # заранее: каталог вместо файла (Docker создаёт КАТАЛОГ при
    # раскомментированном монтировании и отсутствии файла на хосте), пустой
    # файл (New-Item), JSON-экспорт вместо Netscape и UTF-16 из PowerShell.
    dir_case = tmp_path / "cookies.txt"
    dir_case.mkdir()  # именно каталог с именем cookies.txt
    empty = tmp_path / "empty.txt"
    empty.write_text("", encoding="utf-8")
    json_file = tmp_path / "json.txt"
    json_file.write_text('[{"name": "VISITOR_INFO1_LIVE", "value": "abc"}]', encoding="utf-8")
    utf16 = tmp_path / "utf16.txt"
    utf16.write_bytes("# Netscape HTTP Cookie File\n".encode("utf-16"))

    original = downloader.YTDLP_COOKIES_FILE
    try:
        for broken, hint in (
            (dir_case, "каталог, а не файл"),
            (empty, "Netscape"),
            (json_file, "Netscape"),
            (utf16, "UTF-8"),
        ):
            downloader.YTDLP_COOKIES_FILE = str(broken)
            with pytest.raises(downloader.DownloadError) as exc_info:
                downloader._assert_cookies_readable()
            assert hint in str(exc_info.value)
            message = str(exc_info.value)
            assert str(broken) in message or repr(str(broken)) in message
    finally:
        downloader.YTDLP_COOKIES_FILE = original


def test_private_cookies_copy_isolated_per_run(tmp_path, monkeypatch):
    # yt-dlp перезаписывает cookiefile при каждом запуске (close() →
    # save_cookies()), поэтому несколько одновременных запросов с одним
    # YTDLP_COOKIES_FILE писали бы в один файл — гонка и порча. Каждому
    # запуску выдаётся личная копия: в команду уходит путь копии, исходник
    # не пишется (поэтому работают и «:ro»-монтирования, и docker secrets),
    # копия удаляется после запуска.
    source = tmp_path / "cookies.txt"
    source.write_text(
        "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t0\tA\tB\n",
        encoding="utf-8",
    )
    source.chmod(0o444)  # только чтение: исходник никто не перезаписывает
    original = downloader.YTDLP_COOKIES_FILE
    downloader.YTDLP_COOKIES_FILE = str(source)
    try:
        # Право записи больше не проверяется: «:ro» не препятствие.
        downloader._assert_cookies_readable()

        captured = {}

        def fake_run(command, timeout):
            captured["command"] = list(command)
            copy = Path(command[command.index("--cookies") + 1])
            captured["copy_content"] = copy.read_text(encoding="utf-8")  # копия жива во время запуска
            return subprocess.CompletedProcess(args=[], returncode=0, stdout="{}", stderr="")

        monkeypatch.setattr(downloader, "_run", fake_run)
        downloader._yt_dlp_metadata("https://www.youtube.com/watch?v=abc123")
        i = captured["command"].index("--cookies")
        used = Path(captured["command"][i + 1])
        assert used != source  # в команду идёт копия, а не общий файл
        assert captured["copy_content"].startswith("# Netscape")  # содержимое скопировано
        assert not used.exists()  # копия удалена после запуска
        assert source.read_text(encoding="utf-8").startswith("# Netscape")  # исходник не тронут
    finally:
        downloader.YTDLP_COOKIES_FILE = original
        source.chmod(0o644)
