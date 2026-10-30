"""Configuration Management Module for ARX-cutShor."""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _str_to_bool(val: str, default: bool = False) -> bool:
    if val is None:
        return default
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _parse_int_list(val: str, default: List[int]) -> List[int]:
    if not val or not val.strip():
        return default
    result = []
    for item in val.split(","):
        item = item.strip()
        if item.isdigit():
            result.append(int(item))
    return result or default


def _parse_str_list(val: str, default: List[str]) -> List[str]:
    if not val or not val.strip():
        return default
    return [item.strip() for item in val.split(",") if item.strip()]


@dataclass
class AppConfig:
    """Centralized configuration for ARX-cutShor."""

    base_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    app_env: str = field(default_factory=lambda: os.getenv("APP_ENV", "development"))
    dry_run: bool = field(default_factory=lambda: _str_to_bool(os.getenv("DRY_RUN", "true"), True))
    publishing_mode: str = field(default_factory=lambda: os.getenv("PUBLISHING_MODE", "testing"))
    allow_public_publishing: bool = field(
        default_factory=lambda: _str_to_bool(os.getenv("ALLOW_PUBLIC_PUBLISHING", "false"), False)
    )

    # Paths
    database_path: Path = field(default_factory=lambda: Path(os.getenv("DATABASE_PATH", "data/db/arx_cutshor.db")))
    workspace_dir: Path = field(default_factory=lambda: Path(os.getenv("WORKSPACE_DIR", "data/workspace")))
    assets_dir: Path = field(default_factory=lambda: Path(os.getenv("ASSETS_DIR", "data/assets")))
    reports_dir: Path = field(default_factory=lambda: Path(os.getenv("REPORTS_DIR", "data/reports")))
    logs_dir: Path = field(default_factory=lambda: Path(os.getenv("LOGS_DIR", "logs")))
    max_temp_storage_mb: int = field(default_factory=lambda: int(os.getenv("MAX_TEMP_STORAGE_MB", "5000")))
    auto_cleanup_temp: bool = field(default_factory=lambda: _str_to_bool(os.getenv("AUTO_CLEANUP_TEMP", "true"), True))

    # FFmpeg
    ffmpeg_path: str = field(default_factory=lambda: os.getenv("FFMPEG_PATH", "ffmpeg"))
    ffprobe_path: str = field(default_factory=lambda: os.getenv("FFPROBE_PATH", "ffprobe"))

    # Google Drive
    gdrive_enabled: bool = field(default_factory=lambda: _str_to_bool(os.getenv("GDRIVE_ENABLED", "false"), False))
    gdrive_client_secret_file: Path = field(
        default_factory=lambda: Path(os.getenv("GDRIVE_CLIENT_SECRET_FILE", "credentials/gdrive_client_secret.json"))
    )
    gdrive_token_file: Path = field(
        default_factory=lambda: Path(os.getenv("GDRIVE_TOKEN_FILE", "credentials/gdrive_token.json"))
    )
    gdrive_root_folder_id: str = field(default_factory=lambda: os.getenv("GDRIVE_ROOT_FOLDER_ID", ""))
    gdrive_input_folder_id: str = field(default_factory=lambda: os.getenv("GDRIVE_INPUT_FOLDER_ID", ""))
    gdrive_processing_folder_id: str = field(default_factory=lambda: os.getenv("GDRIVE_PROCESSING_FOLDER_ID", ""))
    gdrive_ready_folder_id: str = field(default_factory=lambda: os.getenv("GDRIVE_READY_FOLDER_ID", ""))
    gdrive_published_folder_id: str = field(default_factory=lambda: os.getenv("GDRIVE_PUBLISHED_FOLDER_ID", ""))
    gdrive_failed_folder_id: str = field(default_factory=lambda: os.getenv("GDRIVE_FAILED_FOLDER_ID", ""))
    gdrive_assets_folder_id: str = field(default_factory=lambda: os.getenv("GDRIVE_ASSETS_FOLDER_ID", ""))
    gdrive_reports_folder_id: str = field(default_factory=lambda: os.getenv("GDRIVE_REPORTS_FOLDER_ID", ""))

    # Video Splitting & Processing
    split_mode: str = field(default_factory=lambda: os.getenv("SPLIT_MODE", "fixed"))
    target_clip_duration_sec: float = field(
        default_factory=lambda: float(os.getenv("TARGET_CLIP_DURATION_SEC", "55.0"))
    )
    min_clip_duration_sec: float = field(
        default_factory=lambda: float(os.getenv("MIN_CLIP_DURATION_SEC", "15.0"))
    )
    max_clip_duration_sec: float = field(
        default_factory=lambda: float(os.getenv("MAX_CLIP_DURATION_SEC", "59.0"))
    )
    safe_end_margin_sec: float = field(
        default_factory=lambda: float(os.getenv("SAFE_END_MARGIN_SEC", "0.5"))
    )
    scene_threshold: float = field(
        default_factory=lambda: float(os.getenv("SCENE_THRESHOLD", "0.35"))
    )
    output_width: int = field(default_factory=lambda: int(os.getenv("OUTPUT_WIDTH", "1080")))
    output_height: int = field(default_factory=lambda: int(os.getenv("OUTPUT_HEIGHT", "1920")))
    output_fps: int = field(default_factory=lambda: int(os.getenv("OUTPUT_FPS", "30")))

    # Channel & Rights
    channel_name: str = field(default_factory=lambda: os.getenv("CHANNEL_NAME", "ARX Shorts"))
    default_series_name: str = field(default_factory=lambda: os.getenv("DEFAULT_SERIES_NAME", "السلسلة الرئيسية"))
    default_language: str = field(default_factory=lambda: os.getenv("DEFAULT_LANGUAGE", "ar"))
    require_rights_confirmation: bool = field(
        default_factory=lambda: _str_to_bool(os.getenv("REQUIRE_RIGHTS_CONFIRMATION", "true"), True)
    )

    # AI Provider
    ai_provider: str = field(default_factory=lambda: os.getenv("AI_PROVIDER", "none"))
    ai_api_key: str = field(default_factory=lambda: os.getenv("AI_API_KEY", ""))
    ai_model: str = field(default_factory=lambda: os.getenv("AI_MODEL", ""))

    # Scheduling
    timezone: str = field(default_factory=lambda: os.getenv("TIMEZONE", "Asia/Riyadh"))
    daily_clip_limit: int = field(default_factory=lambda: int(os.getenv("DAILY_CLIP_LIMIT", "3")))
    allowed_publish_days: List[int] = field(
        default_factory=lambda: _parse_int_list(os.getenv("ALLOWED_PUBLISH_DAYS", "0,1,2,3,4,5,6"), [0, 1, 2, 3, 4, 5, 6])
    )
    publish_hours: List[str] = field(
        default_factory=lambda: _parse_str_list(os.getenv("PUBLISH_HOURS", "14:00,18:00,21:00"), ["14:00", "18:00", "21:00"])
    )
    min_interval_minutes: int = field(default_factory=lambda: int(os.getenv("MIN_INTERVAL_MINUTES", "120")))
    max_retry_attempts: int = field(default_factory=lambda: int(os.getenv("MAX_RETRY_ATTEMPTS", "3")))

    # YouTube
    youtube_enabled: bool = field(default_factory=lambda: _str_to_bool(os.getenv("YOUTUBE_ENABLED", "false"), False))
    youtube_client_secret_file: Path = field(
        default_factory=lambda: Path(os.getenv("YOUTUBE_CLIENT_SECRET_FILE", "credentials/youtube_client_secret.json"))
    )
    youtube_token_file: Path = field(
        default_factory=lambda: Path(os.getenv("YOUTUBE_TOKEN_FILE", "credentials/youtube_token.json"))
    )
    youtube_category_id: str = field(default_factory=lambda: os.getenv("YOUTUBE_CATEGORY_ID", "22"))
    youtube_default_privacy: str = field(default_factory=lambda: os.getenv("YOUTUBE_DEFAULT_PRIVACY", "private"))

    # Web UI
    web_host: str = field(default_factory=lambda: os.getenv("WEB_HOST", "127.0.0.1"))
    web_port: int = field(default_factory=lambda: int(os.getenv("WEB_PORT", "8000")))
    admin_access_token: str = field(
        default_factory=lambda: os.getenv("ADMIN_ACCESS_TOKEN", "arx_local_dev_token_change_me")
    )

    # Notifications
    notification_webhook_url: str = field(default_factory=lambda: os.getenv("NOTIFICATION_WEBHOOK_URL", ""))
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = field(default_factory=lambda: os.getenv("TELEGRAM_CHAT_ID", ""))

    def __post_init__(self):
        # Resolve relative paths against base_dir
        if not self.database_path.is_absolute():
            self.database_path = (self.base_dir / self.database_path).resolve()
        if not self.workspace_dir.is_absolute():
            self.workspace_dir = (self.base_dir / self.workspace_dir).resolve()
        if not self.assets_dir.is_absolute():
            self.assets_dir = (self.base_dir / self.assets_dir).resolve()
        if not self.reports_dir.is_absolute():
            self.reports_dir = (self.base_dir / self.reports_dir).resolve()
        if not self.logs_dir.is_absolute():
            self.logs_dir = (self.base_dir / self.logs_dir).resolve()
        if not self.gdrive_client_secret_file.is_absolute():
            self.gdrive_client_secret_file = (self.base_dir / self.gdrive_client_secret_file).resolve()
        if not self.gdrive_token_file.is_absolute():
            self.gdrive_token_file = (self.base_dir / self.gdrive_token_file).resolve()
        if not self.youtube_client_secret_file.is_absolute():
            self.youtube_client_secret_file = (self.base_dir / self.youtube_client_secret_file).resolve()
        if not self.youtube_token_file.is_absolute():
            self.youtube_token_file = (self.base_dir / self.youtube_token_file).resolve()

        # Auto-discover ffmpeg from imageio_ffmpeg if system ffmpeg is unavailable
        self._resolve_ffmpeg()

    def _resolve_ffmpeg(self):
        import shutil
        if shutil.which(self.ffmpeg_path) is None:
            try:
                import imageio_ffmpeg
                exe = imageio_ffmpeg.get_ffmpeg_exe()
                if exe and Path(exe).exists():
                    self.ffmpeg_path = exe
            except Exception:
                pass

    def ensure_directories(self):
        """Ensure all required local workspace and data directories exist."""
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        for sub in ["input", "processing", "ready", "published", "failed", "temp"]:
            (self.workspace_dir / sub).mkdir(parents=True, exist_ok=True)
        for asset_sub in ["logo", "intro", "outro", "music"]:
            (self.assets_dir / asset_sub).mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)

    def get_effective_privacy(self, requested_privacy: Optional[str] = None) -> str:
        """Enforce safety gates: never allow public publishing during testing or without explicit approval."""
        if self.dry_run or self.publishing_mode == "testing" or not self.allow_public_publishing:
            if requested_privacy == "unlisted":
                return "unlisted"
            return "private"
        return requested_privacy or self.youtube_default_privacy


def load_config(base_dir: Optional[Path] = None) -> AppConfig:
    if base_dir:
        cfg = AppConfig(base_dir=Path(base_dir).resolve())
    else:
        cfg = AppConfig()
    cfg.ensure_directories()
    return cfg

