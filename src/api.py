from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
from typing import Annotated

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, ConfigDict, HttpUrl

from src import pipeline
from src.config import MAX_UPLOAD_BYTES
from src.models import CONTRACT_VERSION, Recipe

app = FastAPI(
    title="Fropti Video-to-Recipe API",
    version="0.1.0",
    description=f"Учебный каркас. Контракт Recipe {CONTRACT_VERSION}. Пайплайн пока не реализован.",
)


class ExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "contract_version": CONTRACT_VERSION}


@app.post(
    "/extract",
    response_model=Recipe,
    responses={400: {"description": "Видео длиннее 180 секунд"}, 501: {"description": "Пайплайн не реализован"}},
)
def extract_recipe(request: ExtractRequest) -> Recipe:
    return _extract(str(request.url))


@app.post(
    "/extract/upload",
    response_model=Recipe,
    responses={
        400: {"description": "Пустой файл или видео длиннее 180 секунд"},
        413: {"description": "Файл больше 100 MiB"},
        501: {"description": "Пайплайн не реализован"},
    },
)
def extract_uploaded_recipe(
    file: Annotated[UploadFile, File(json_schema_extra={"format": "binary"})],
) -> Recipe:
    if not file.size:
        raise HTTPException(status_code=400, detail={"code": "empty_file", "message": "Файл пуст"})
    if file.size > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"code": "file_too_large", "message": "Файл должен быть не больше 100 MiB"},
        )
    with TemporaryDirectory(prefix="fropti-") as directory:
        video_path = Path(directory) / "video"
        with video_path.open("wb") as destination:
            shutil.copyfileobj(file.file, destination, length=1024 * 1024)
        return _extract(video_path)


def _extract(source: str | Path) -> Recipe:
    # TODO: Implement this — добавить согласованные ошибки загрузчика, STT, OCR и LLM.
    try:
        return pipeline.extract_recipe(source)
    except pipeline.VideoTooLongError as exc:
        raise HTTPException(status_code=400, detail={"code": "video_too_long", "message": str(exc)}) from exc
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=501,
            detail={"code": "pipeline_not_implemented", "message": str(exc)},
        ) from exc
