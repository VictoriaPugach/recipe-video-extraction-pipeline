# Two image modes via BUILD_MODE build arg:
#   --build-arg BUILD_MODE=slim   (default) base only: /health + tests; no ML models
#   --build-arg BUILD_MODE=full   installs requirements-ml.txt too: real STT/OCR/LLM/yt-dlp
#
# Slim image:   ~250 MB, builds in ~30 s. Use for CI, contract checks, dev sandbox.
# Full image:   ~5-6 GB, builds in ~15-20 min. Pre-downloads Whisper + both PaddleOCR
#               (ru + en) models so every container start is instant. Use for real extraction.
#
# BASE IMAGE NOTE: Debian Bookworm (12) is current stable with full security
# support (EOL: ~2026-06 for the main suite, ~2028 for LTS). PaddlePaddle 3.x
# (see requirements-ml.txt) has no Bullseye-era zlib segfault (inflateReset2) —
# no archive mirror hacks, no EOL package 404s.

FROM python:3.12-slim-bookworm

ARG BUILD_MODE=slim
ARG WHISPER_MODEL=small
# Space-separated list of faster-whisper models baked into the image. WHISPER_MODEL
# (above) is only the runtime default; any model in this list can be selected via
# .env (WHISPER_MODEL=medium) with no download at the first request. Default
# "small" keeps the image light; to bake medium as well (+~1.5 GB
# to the image, slower build), set "small medium" — without baking, a medium
# .env value downloads it once at the first request into the hf-cache volume.
ARG WHISPER_PRELOAD_MODELS="small"
# ENV propagates the build-arg into runtime: ARG alone is build-scoped, so
# without this line a container built with a non-default WHISPER_MODEL would
# run without the variable, fall back to the "small" default in src/config.py,
# and the baked model would sit unused while the first request downloads small
# (~460 MB). With ENV the runtime default matches what was baked; a value in
# .env still overrides. The compose full build pins the baked model to small
# (see docker-compose.full.yml); medium can be baked via
# --build-arg WHISPER_PRELOAD_MODELS="small medium". No effect in slim mode.
ENV WHISPER_MODEL=${WHISPER_MODEL}
WORKDIR /app

# ffmpeg: media probing (ffprobe) + audio extraction + frame extraction (ffmpeg).
# libgl1 + libglib2.0-0: runtime deps of paddleocr / paddlepaddle.
# libgomp1, libblas3, liblapack3, zlib1g: PaddlePaddle runtime deps that the slim
# image doesn't include by default — installing them avoids runtime segfaults.
#
# nodejs MUST come from NodeSource (22.x), NOT from Debian Bookworm: yt-dlp
# (2026.10+) solves YouTube's player challenges in a JavaScript runtime and
# accepts node only >= 22.0.0 (MIN_SUPPORTED_VERSION in yt_dlp's
# utils/_jsruntime.py; deno >= 2.3 / bun >= 1.2.11 are the alternatives).
# Debian Bookworm's nodejs package is 18.x, which yt-dlp reports as
# unsupported — POST /extract then fails with download_failed and
# "No supported JavaScript runtime could be found". The runtime name is
# configurable at runtime via YTDLP_JS_RUNTIME (src/config.py).
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && install -d -m 0755 /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
       | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" \
       > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends \
         ffmpeg libgl1 libglib2.0-0 libgomp1 libblas3 liblapack3 zlib1g nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-ml.txt ./
COPY packages/fropti-core-contracts ./packages/fropti-core-contracts
RUN pip install --no-cache-dir -r requirements.txt \
    && if [ "$BUILD_MODE" = "full" ]; then pip install --no-cache-dir -r requirements-ml.txt; fi \
    && pip install --no-cache-dir setuptools wheel

# Non-root user and model-cache mountpoints do not depend on source code, so
# they live before the source layers: code edits then never re-run them.
# (COPY runs as root; appuser only needs read access to import the package.)
RUN useradd --create-home appuser
USER appuser
ENV HOME=/home/appuser

# Model-cache dirs must exist in the image BEFORE the predownload step: compose
# mounts named volumes at these paths, and a mountpoint missing from the image
# would be created by Docker as root-owned — appuser could not write into it and
# the first model download would fail with PermissionError (slim image has no
# preloads that would create these dirs on its own).
RUN mkdir -p "$HOME/.cache/huggingface" "$HOME/.paddleocr" "$HOME/.paddlex"

# Minimal source subset needed by the predownload: _get_reader must be the same
# factory as at runtime — it applies the oneDNN protections, and a raw
# PaddleOCR(...) call here could crash the build itself on affected CPUs.
# Keeping this layer tiny and separate means ordinary code edits (pipeline,
# api, downloader, docs…) do NOT invalidate the heavy model-download layer
# below; only edits to config.py or ocr.py re-run the predownload.
COPY src/__init__.py src/config.py src/errors.py ./src/
COPY src/extractors/__init__.py src/extractors/ocr.py ./src/extractors/

# Pre-download ML models as appuser (only in full mode).
# OCR downloads go through src.extractors.ocr._get_reader — the same factory as
# at runtime: it disables oneDNN explicitly for every predictor (Paddle 3.x
# enables MKLDNN by default, which crashes on some CPUs/emulators) and caps the
# oneDNN ISA. A raw PaddleOCR(...) call here would bypass that protection and
# could fail the build itself on an affected machine.
# Both ru and en PaddleOCR models are pre-downloaded so you can switch via .env
# (OCR_LANG=ru or OCR_LANG=en) at runtime without rebuilding.
RUN if [ "$BUILD_MODE" = "full" ]; then \
        for m in $WHISPER_PRELOAD_MODELS; do \
            echo "Pre-downloading faster-whisper model: $m (small ~460 MB, medium ~1.5 GB)..." \
            && python -c "from faster_whisper import WhisperModel; WhisperModel('$m', device='cpu', compute_type='int8')" \
            || exit 1; \
        done \
        && echo "Pre-downloading PaddleOCR model (lang=ru, ~80 MB)..." \
        && python -c "from src.extractors.ocr import _get_reader; _get_reader()" \
        && echo "Pre-downloading PaddleOCR model (lang=en, ~80 MB)..." \
        && OCR_LANG=en python -c "from src.extractors.ocr import _get_reader; _get_reader()"; \
    fi

# Full source last: after the first build, code edits re-run only this ~90 KB
# layer — the heavy apt/pip/model layers above stay cached. Placing it after
# the predownload also guarantees the image runs the complete source tree.
COPY src ./src

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
