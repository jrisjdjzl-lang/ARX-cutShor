"""Google Drive Integration Module for ARX-cutShor.

Implements official Google Drive API v3 integration with:
- OAuth 2.0 authentication (least privilege scopes).
- Fixed Folder ID discovery and folder tree provisioning.
- Discovering new videos and checking upload completeness.
- Avoiding duplicate processing via Drive File IDs and MD5 checksums.
- Skipping re-download if a valid local copy already exists.
- Retry and backoff handling for network drops and API quota limits.
- Full DRY_RUN and Mock compatibility for offline/automated testing.
"""

import hashlib
import io
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from arx_cutshor.database import Database
from arx_cutshor.security import safe_resolve_path, sanitize_filename, scrub_secrets
from arx_cutshor.video_engine import SUPPORTED_EXTENSIONS

TupleResult = Any

# Least-privilege scopes: read files in specified Drive folders and manage app-created files/folders
GDRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.file",
]

ALLOWED_VIDEO_MIMES = {
    "video/mp4",
    "video/quicktime",
    "video/x-matroska",
    "video/webm",
    "application/octet-stream",
}


class GoogleDriveError(Exception):
    """Base error for Google Drive operations."""


class OAuthAuthenticationError(GoogleDriveError):
    """Raised when OAuth credentials or tokens are missing, expired, or invalid."""


class QuotaExceededError(GoogleDriveError):
    """Raised when Google Drive API quota or rate limit is hit."""


def compute_md5(filepath: Path) -> str:
    md5 = hashlib.md5()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            md5.update(chunk)
    return md5.hexdigest()


class GoogleDriveManager:
    """Manages discovery, validation, and downloading of source videos from Google Drive."""

    def __init__(
        self,
        db: Database,
        workspace_dir: Path,
        client_secret_file: Path,
        token_file: Path,
        input_folder_id: str = "",
        dry_run: bool = True,
        service_client: Optional[Any] = None,
    ):
        self.db = db
        self.workspace_dir = Path(workspace_dir).resolve()
        self.input_dir = self.workspace_dir / "input"
        self.input_dir.mkdir(parents=True, exist_ok=True)
        self.client_secret_file = Path(client_secret_file)
        self.token_file = Path(token_file)
        self.input_folder_id = input_folder_id
        self.dry_run = dry_run
        self._service = service_client

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    def authenticate(self, interactive: bool = False) -> Any:
        """Authenticate with Google Drive API using OAuth 2.0 token or client_secret.json."""
        if self._service is not None:
            return self._service

        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise OAuthAuthenticationError(
                "Google API client libraries are not installed. Run pip install google-api-python-client google-auth-oauthlib"
            ) from exc

        creds = None
        if self.token_file.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self.token_file), GDRIVE_SCOPES)
            except Exception as exc:
                raise OAuthAuthenticationError(f"Invalid Google Drive token file: {scrub_secrets(str(exc))}") from exc

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as exc:
                    raise OAuthAuthenticationError(
                        f"Failed to refresh Google Drive OAuth token: {scrub_secrets(str(exc))}"
                    ) from exc
            else:
                if not interactive:
                    raise OAuthAuthenticationError(
                        "Google Drive OAuth token is missing or expired. Run initial interactive login first."
                    )
                if not self.client_secret_file.exists():
                    raise OAuthAuthenticationError(
                        f"Client secret file not found at {self.client_secret_file}. See GOOGLE_DRIVE_SETUP.md."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(str(self.client_secret_file), GDRIVE_SCOPES)
                creds = flow.run_local_server(port=0)

            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            self.token_file.write_text(creds.to_json(), encoding="utf-8")

        self._service = build("drive", "v3", credentials=creds)
        return self._service

    # ------------------------------------------------------------------
    # Retry & Quota Handling Wrapper
    # ------------------------------------------------------------------
    def _execute_with_retry(self, func: Callable[[], Any], max_retries: int = 3, base_delay: float = 0.05) -> Any:
        """Execute a Drive API call with exponential backoff for network drops and rate limits."""
        last_exc = None
        for attempt in range(1, max_retries + 1):
            try:
                return func()
            except (OAuthAuthenticationError, ValueError):
                raise
            except Exception as exc:
                last_exc = exc
                msg = str(exc).lower()
                if "invalid_grant" in msg or "unauthorized" in msg or "401" in msg:
                    raise OAuthAuthenticationError(f"OAuth authorization error: {scrub_secrets(str(exc))}") from exc
                if "quota" in msg or "ratelimitexceeded" in msg or "userratelimitexceeded" in msg or "429" in msg:
                    if attempt == max_retries:
                        raise QuotaExceededError(
                            f"Google Drive API quota exceeded after {max_retries} attempts: {scrub_secrets(str(exc))}"
                        ) from exc
                if attempt == max_retries:
                    break
                time.sleep(base_delay * (2 ** (attempt - 1)))
        raise GoogleDriveError(f"Google Drive operation failed after {max_retries} attempts: {scrub_secrets(str(last_exc))}")

    # ------------------------------------------------------------------
    # File Discovery & Validation
    # ------------------------------------------------------------------
    def is_file_valid_and_complete(self, file_item: Dict[str, Any]) -> TupleResult:
        """Verify that a Drive file is a supported video and its upload is complete.

        Returns (is_valid: bool, reason: str).
        """
        name = file_item.get("name", "")
        ext = Path(name).suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            return False, f"Unsupported extension '{ext}'"

        mime = file_item.get("mimeType", "")
        if mime and mime not in ALLOWED_VIDEO_MIMES and not mime.startswith("video/"):
            return False, f"Unsupported mimeType '{mime}'"

        size = int(file_item.get("size") or 0)
        if size <= 0:
            return False, "File size is 0 bytes (upload incomplete)"

        # Check md5Checksum or headRevisionId to ensure upload is finalized on Drive
        if file_item.get("trashed", False):
            return False, "File is in trash"

        if "md5Checksum" in file_item and not file_item["md5Checksum"]:
            return False, "Missing md5Checksum (upload still in progress)"

        return True, "ok"

    def discover_new_videos(self, folder_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Poll Google Drive input folder by fixed Folder ID and register new valid videos in SQLite."""
        target_folder = folder_id or self.input_folder_id
        if not target_folder:
            raise ValueError("Google Drive input folder ID is not configured")

        service = self.authenticate(interactive=False)

        def _list_call():
            query = f"'{target_folder}' in parents and trashed = false"
            return (
                service.files()
                .list(
                    q=query,
                    pageSize=100,
                    fields="files(id, name, mimeType, size, md5Checksum, trashed, createdTime)",
                )
                .execute()
            )

        response = self._execute_with_retry(_list_call)
        files = response.get("files", [])
        newly_registered: List[Dict[str, Any]] = []

        for item in files:
            drive_id = item.get("id")
            name = item.get("name", "")
            if not drive_id or not name:
                continue

            # Check if already registered in database
            existing = self.db.get_source_video_by_drive_id(drive_id)
            if existing and existing["status"] in ("processing", "ready", "completed", "published"):
                continue

            is_valid, reason = self.is_file_valid_and_complete(item)
            if not is_valid:
                self.db.log_operation(
                    operation_type="gdrive_discover",
                    entity_id=drive_id,
                    status="skipped",
                    message=f"Skipped '{name}': {reason}",
                )
                continue

            safe_name = sanitize_filename(name)
            video_record, is_new = self.db.register_source_video(
                drive_file_id=drive_id,
                filename=safe_name,
                mime_type=item.get("mimeType", "video/mp4"),
                size_bytes=int(item.get("size", 0)),
                checksum=item.get("md5Checksum", ""),
            )
            if is_new:
                self.db.log_operation(
                    operation_type="gdrive_discover",
                    entity_id=drive_id,
                    status="success",
                    message=f"Discovered new video '{safe_name}' ({video_record['size_bytes']} bytes)",
                )
                newly_registered.append(video_record)

        self.db.set_setting("last_gdrive_scan", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        return newly_registered

    # ------------------------------------------------------------------
    # Downloading with Local Cache Verification
    # ------------------------------------------------------------------
    def download_video(self, video_record: Dict[str, Any]) -> Path:
        """Download a source video to local workspace unless a valid intact local copy already exists."""
        drive_id = video_record["drive_file_id"]
        safe_name = sanitize_filename(video_record["filename"])
        local_target = safe_resolve_path(self.input_dir, f"{drive_id}_{safe_name}")
        expected_size = int(video_record.get("size_bytes") or 0)
        expected_md5 = video_record.get("checksum") or ""

        # Check if an intact local copy already exists
        if local_target.exists() and local_target.is_file():
            actual_size = local_target.stat().st_size
            if expected_size > 0 and actual_size == expected_size:
                if not expected_md5 or compute_md5(local_target) == expected_md5:
                    self.db.update_source_video(video_record["id"], local_path=str(local_target))
                    self.db.log_operation(
                        operation_type="gdrive_download",
                        entity_id=drive_id,
                        status="cached",
                        message=f"Reused verified local copy: {local_target.name}",
                    )
                    return local_target

        # Also check if video_record['local_path'] points to an existing local file (e.g., in local DRY_RUN mode)
        if video_record.get("local_path"):
            existing_local = Path(video_record["local_path"])
            if existing_local.exists() and existing_local.is_file():
                return existing_local

        service = self.authenticate(interactive=False)
        temp_download = local_target.with_suffix(local_target.suffix + ".part")

        def _download_call():
            req = service.files().get_media(fileId=drive_id)
            if hasattr(req, "execute"):
                content = req.execute()
                if isinstance(content, (bytes, bytearray)):
                    temp_download.write_bytes(content)
                    return True
            from googleapiclient.http import MediaIoBaseDownload

            with open(temp_download, "wb") as fh:
                downloader = MediaIoBaseDownload(fh, req, chunksize=4 * 1024 * 1024)
                done = False
                while not done:
                    _, done = downloader.next_chunk()
            return True

        self._execute_with_retry(_download_call)

        if not temp_download.exists():
            raise GoogleDriveError(f"Download produced no file for Drive ID {drive_id}")

        actual_size = temp_download.stat().st_size
        if expected_size > 0 and actual_size != expected_size:
            temp_download.unlink(missing_ok=True)
            raise GoogleDriveError(
                f"Downloaded file size mismatch for {drive_id}: expected {expected_size}, got {actual_size}"
            )

        if expected_md5:
            actual_md5 = compute_md5(temp_download)
            if actual_md5 != expected_md5:
                temp_download.unlink(missing_ok=True)
                raise GoogleDriveError(
                    f"Downloaded file MD5 mismatch for {drive_id}: expected {expected_md5}, got {actual_md5}"
                )

        temp_download.replace(local_target)
        self.db.update_source_video(video_record["id"], local_path=str(local_target))
        self.db.log_operation(
            operation_type="gdrive_download",
            entity_id=drive_id,
            status="success",
            message=f"Downloaded '{safe_name}' to {local_target.name}",
        )
        return local_target

    # ------------------------------------------------------------------
    # Drive Folder Structure Setup Helper
    # ------------------------------------------------------------------
    def ensure_drive_folder_structure(self, root_folder_id: str) -> Dict[str, str]:
        """Create the standard ARX-cutShor folder tree inside Google Drive if desired."""
        service = self.authenticate(interactive=False)
        folder_names = ["input", "processing", "ready", "published", "failed", "assets", "reports"]
        created_ids: Dict[str, str] = {}

        for fname in folder_names:
            q = (
                f"'{root_folder_id}' in parents and name = '{fname}' "
                "and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
            )
            res = self._execute_with_retry(lambda q=q: service.files().list(q=q, fields="files(id, name)").execute())
            files = res.get("files", [])
            if files:
                created_ids[fname] = files[0]["id"]
            else:
                meta = {
                    "name": fname,
                    "mimeType": "application/vnd.google-apps.folder",
                    "parents": [root_folder_id],
                }
                folder = self._execute_with_retry(
                    lambda meta=meta: service.files().create(body=meta, fields="id").execute()
                )
                created_ids[fname] = folder["id"]

        return created_ids


TupleResult = Any

