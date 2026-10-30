"""Security, Privacy, and Logging Module for ARX-cutShor.

Provides:
- Secret scrubbing in logs and error messages.
- Filename sanitization and Path Traversal prevention.
- Temporary file cleanup policy.
"""

import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

# Patterns that look like tokens, keys, or secrets
SECRET_PATTERNS = [
    re.compile(r"(ya29\.[0-9A-Za-z\-_]+)", re.IGNORECASE),
    re.compile(r"(AIza[0-9A-Za-z\-_]{30,})", re.IGNORECASE),
    re.compile(r"(1//[0-9A-Za-z\-_]{20,})", re.IGNORECASE),
    re.compile(r"(sk-[0-9A-Za-z\-_]{20,})", re.IGNORECASE),
    re.compile(r"(ghp_[0-9A-Za-z]{20,})", re.IGNORECASE),
    re.compile(r"(github_pat_[0-9A-Za-z_]{20,})", re.IGNORECASE),
    re.compile(r'("?(?:access_token|refresh_token|client_secret|api_key|password|token|authorization)"?\s*[:=]\s*["\']?)([^"\'\s,}]+)', re.IGNORECASE),
]


def scrub_secrets(text: str) -> str:
    """Remove or mask any potential OAuth tokens, API keys, or passwords from log strings."""
    if not text:
        return ""
    scrubbed = str(text)
    for pattern in SECRET_PATTERNS[:-1]:
        scrubbed = pattern.sub("[REDACTED_SECRET]", scrubbed)
    # Key-value secret pattern
    scrubbed = SECRET_PATTERNS[-1].sub(r"\1[REDACTED]", scrubbed)
    return scrubbed


class SecretScrubbingFilter(logging.Filter):
    """Logging filter that automatically scrubs secrets from log messages and args."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = scrub_secrets(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: scrub_secrets(str(v)) if isinstance(v, str) else v for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(scrub_secrets(str(a)) if isinstance(a, str) else a for a in record.args)
        return True


def setup_logger(name: str = "arx_cutshor", log_dir: Optional[Path] = None) -> logging.Logger:
    """Configure and return a secure logger with secret scrubbing."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s | %(message)s")
    scrub_filter = SecretScrubbingFilter()

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    stream_handler.addFilter(scrub_filter)
    logger.addHandler(stream_handler)

    if log_dir:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_dir / "arx_cutshor.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(scrub_filter)
        logger.addHandler(file_handler)

    return logger


def sanitize_filename(filename: str) -> str:
    """Sanitize an untrusted filename: prevent path traversal, strip dangerous characters, preserve Arabic/Latin."""
    if not filename:
        raise ValueError("Empty filename is not allowed")

    # Strip directory components
    base = os.path.basename(filename.replace("\\", "/")).strip()
    if not base or base in (".", "..") or ".." in filename.replace("\\", "/").split("/"):
        raise ValueError(f"Unsafe filename or path traversal detected: {filename}")

    # Replace null bytes and control characters
    base = re.sub(r"[\x00-\x1f\x7f]", "", base)
    # Replace shell/filesystem hazardous characters while keeping Arabic, letters, numbers, spaces, dots, hyphens, underscores
    base = re.sub(r'[<>:"/\\|?*`;$&|]', "_", base)
    # Collapse multiple spaces or underscores
    base = re.sub(r"\s+", "_", base)
    if base.startswith("."):
        base = "file" + base
    return base


def safe_resolve_path(base_dir: Path, target_path: str | Path) -> Path:
    """Resolve target_path inside base_dir and guarantee no Path Traversal occurs."""
    base_resolved = Path(base_dir).resolve()
    target_str = str(target_path)
    # Reject explicit parent traversal attempts
    norm_parts = target_str.replace("\\", "/").split("/")
    if ".." in norm_parts:
        raise PermissionError(f"Path traversal attempt blocked: {target_path}")

    candidate = (base_resolved / target_path).resolve()
    try:
        candidate.relative_to(base_resolved)
    except ValueError as exc:
        raise PermissionError(f"Path '{target_path}' escapes allowed base directory '{base_resolved}'") from exc
    return candidate


def cleanup_temp_files(temp_dir: Path, max_age_seconds: float = 3600, max_total_mb: float = 5000) -> int:
    """Clean up temporary files older than max_age_seconds or exceeding max_total_mb.

    Returns the number of deleted files.
    """
    temp_dir = Path(temp_dir)
    if not temp_dir.exists():
        return 0

    deleted_count = 0
    now = time.time()
    files_info = []

    for root, _, filenames in os.walk(temp_dir):
        for fname in filenames:
            fpath = Path(root) / fname
            try:
                stat = fpath.stat()
                if max_age_seconds >= 0 and (now - stat.st_mtime) >= max_age_seconds:
                    fpath.unlink()
                    deleted_count += 1
                else:
                    files_info.append((stat.st_mtime, stat.st_size, fpath))
            except OSError:
                continue

    # Enforce storage cap if needed
    total_bytes = sum(size for _, size, _ in files_info)
    max_bytes = max_total_mb * 1024 * 1024
    if total_bytes > max_bytes:
        # Sort oldest first
        files_info.sort(key=lambda x: x[0])
        for _, size, fpath in files_info:
            if total_bytes <= max_bytes:
                break
            try:
                fpath.unlink()
                total_bytes -= size
                deleted_count += 1
            except OSError:
                continue

    return deleted_count
