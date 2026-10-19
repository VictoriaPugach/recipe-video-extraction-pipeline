import os

from dotenv import load_dotenv

load_dotenv()

CONTRACT_MODE = os.getenv("CONTRACT_MODE", "draft")
MAX_VIDEO_SECONDS = 180
MAX_UPLOAD_BYTES = 100 * 1024 * 1024

if CONTRACT_MODE not in {"draft", "shared"}:
    raise ValueError("CONTRACT_MODE должен быть draft или shared")
