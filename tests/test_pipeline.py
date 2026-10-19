import json
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


def test_extract_reports_unimplemented_pipeline():
    response = client.post("/extract", json={"url": VIDEO_URL})
    assert response.status_code == 501
    assert response.json()["detail"]["code"] == "pipeline_not_implemented"


@pytest.mark.parametrize(("duration", "status"), [(179.9, 501), (180, 501), (180.01, 400)])
def test_duration_boundary(monkeypatch, duration, status):
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


@pytest.mark.parametrize(("url", "code"), [(VIDEO_URL, 1), ("not-a-url", 2)])
def test_cli_reports_error_without_recipe(url, code):
    result = subprocess.run(
        [sys.executable, "-m", "src.main", url], cwd=ROOT, capture_output=True, text=True, check=False,
    )
    assert result.returncode == code
    assert result.stdout == ""
    assert result.stderr
    assert "Traceback" not in result.stderr
