import os

from dotenv import load_dotenv

load_dotenv()

CONTRACT_MODE = os.getenv("CONTRACT_MODE", "draft")
MAX_VIDEO_SECONDS = 180

if CONTRACT_MODE not in {"draft", "shared"}:
    raise ValueError("CONTRACT_MODE должен быть draft или shared")
