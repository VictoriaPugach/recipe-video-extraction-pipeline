from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, HttpUrl

from src import pipeline
from src.config import CONTRACT_MODE
from src.models import Recipe

app = FastAPI(
    title="Fropti Video-to-Recipe API",
    version="0.1.0",
    description=f"Учебный каркас. Режим контракта: {CONTRACT_MODE}. Пайплайн пока не реализован.",
)


class ExtractRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "contract_mode": CONTRACT_MODE}


@app.post(
    "/extract",
    response_model=Recipe,
    responses={400: {"description": "Видео длиннее 180 секунд"}, 501: {"description": "Пайплайн не реализован"}},
)
def extract_recipe(request: ExtractRequest) -> Recipe:
    # TODO: Implement this — добавить согласованные ошибки загрузчика, STT, OCR и LLM.
    try:
        return pipeline.extract_recipe(str(request.url))
    except pipeline.VideoTooLongError as exc:
        raise HTTPException(status_code=400, detail={"code": "video_too_long", "message": str(exc)}) from exc
    except NotImplementedError as exc:
        raise HTTPException(
            status_code=501,
            detail={"code": "pipeline_not_implemented", "message": str(exc)},
        ) from exc
