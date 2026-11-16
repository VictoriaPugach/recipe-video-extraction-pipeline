# Единая иерархия прикладных ошибок: HTTP-статус и машинный код хранятся
# на классе, поэтому api.py и CLI преобразуют ошибки без дублирования карт.


class AppError(RuntimeError):
    code = "internal_error"
    http_status = 500


class VideoUnreadableError(AppError):
    code = "video_unreadable"
    http_status = 400


class ModelUnavailableError(AppError):
    code = "model_unavailable"
    http_status = 503


class MediaProcessingError(AppError):
    code = "media_processing_failed"
    http_status = 500


class OcrError(AppError):
    code = "ocr_failed"
    http_status = 500


class NoUsableContentError(AppError):
    code = "no_usable_content"
    http_status = 422


class LLMUnavailableError(AppError):
    code = "llm_unavailable"
    http_status = 502


class LLMInvalidResponseError(AppError):
    code = "llm_invalid_response"
    http_status = 502


class DownloaderUnavailableError(AppError):
    # yt-dlp не установлен или не запускается — проблема окружения сервера,
    # а не входных данных: 500, а не 502 download_failed. Симметрично
    # ToolNotFoundError у ffprobe (→ media_processing_failed).
    code = "downloader_unavailable"
    http_status = 500
