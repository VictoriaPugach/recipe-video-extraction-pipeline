import json
from pathlib import Path
import subprocess
import sys

from fastapi.testclient import TestClient
import pytest

from src import api, downloader, main, pipeline
from src.models import Recipe

ROOT = Path(__file__).resolve().parents[1]
client = TestClient(api.app)


def test_openapi_offers_file_upload():
    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/extract/upload"]["post"]
    body = operation["requestBody"]["content"]["multipart/form-data"]["schema"]
    model = schema["components"]["schemas"][body["$ref"].split("/")[-1]]
    assert model["required"] == ["file"]
    assert model["properties"]["file"]["format"] == "binary"


def test_upload_requires_file():
    assert client.post("/extract/upload").status_code == 422


@pytest.mark.parametrize(("content", "status", "code"), [
    (b"", 400, "empty_file"),
    (b"1234", 501, "pipeline_not_implemented"),
    (b"12345", 413, "file_too_large"),
])
def test_upload_size_limits(monkeypatch, content, status, code):
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 4)
    response = client.post("/extract/upload", files={"file": ("sample.mp4", content, "video/mp4")})
    assert response.status_code == status
    assert response.json()["detail"]["code"] == code


def test_upload_passes_file_and_cleans_up_after_success(monkeypatch):
    paths = []
    recipe_data = json.loads((ROOT / "data/expected_outputs/pasta.json").read_text())
    recipe_data["source"] = {"type": "upload"}
    recipe = Recipe.model_validate(recipe_data)
    assert recipe.source.type == "upload"

    def extract(source):
        assert isinstance(source, Path)
        assert source.read_bytes() == b"uploaded content"
        assert source.name == "video"
        paths.append(source)
        return recipe

    monkeypatch.setattr(pipeline, "extract_recipe", extract)
    response = client.post(
        "/extract/upload", files={"file": ("../../sample.mp4", b"uploaded content", "video/mp4")},
    )
    assert response.status_code == 200
    assert response.json() == recipe.model_dump(mode="json")
    assert len(paths) == 1
    assert not paths[0].parent.exists()


@pytest.mark.parametrize(("duration", "status"), [(180, 501), (180.01, 400)])
def test_upload_duration_limit_and_cleanup(monkeypatch, duration, status):
    paths = []

    def probe(source):
        assert isinstance(source, Path)
        assert source.read_bytes() == b"video"
        paths.append(source)
        return duration

    monkeypatch.setattr(downloader, "probe_duration", probe)
    response = client.post("/extract/upload", files={"file": ("sample.mp4", b"video", "video/mp4")})
    assert response.status_code == status
    assert len(paths) == 1
    assert not paths[0].parent.exists()


def test_upload_cleanup_after_unexpected_error(monkeypatch):
    paths = []

    def extract(source):
        paths.append(source)
        raise RuntimeError("Processing failed")

    monkeypatch.setattr(pipeline, "extract_recipe", extract)
    with TestClient(api.app, raise_server_exceptions=False) as error_client:
        response = error_client.post("/extract/upload", files={"file": ("sample.mp4", b"video")})
    assert response.status_code == 500
    assert len(paths) == 1
    assert not paths[0].parent.exists()


@pytest.mark.parametrize("arguments", [[], ["--file", "missing.mp4"], ["https://example.org/v", "--file", "v.mp4"]])
def test_cli_requires_one_valid_source(arguments):
    result = subprocess.run(
        [sys.executable, "-m", "src.main", *arguments],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 2
    assert "Traceback" not in result.stderr


def test_cli_passes_local_file_to_pipeline(monkeypatch, tmp_path, capsys):
    video_path = tmp_path / "sample.mp4"
    video_path.write_bytes(b"video")
    paths = []

    def extract(source):
        paths.append(source)
        raise NotImplementedError("Local extraction is not implemented")

    monkeypatch.setattr(pipeline, "extract_recipe", extract)
    monkeypatch.setattr(sys, "argv", ["fropti", "--file", str(video_path)])
    assert main.main() == 1
    assert paths == [video_path]
    assert video_path.read_bytes() == b"video"
    assert "Local extraction is not implemented" in capsys.readouterr().err
