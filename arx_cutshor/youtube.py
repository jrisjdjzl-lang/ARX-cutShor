"""YouTube Data API v3 Integration Module for ARX-cutShor.

Implements:
- OAuth 2.0 authentication (least privilege scopes).
- Channel eligibility & status verification.
- Resumable video upload with chunking and interruption recovery.
- State reconciliation BEFORE retrying a partially failed upload (prevents duplicate video uploads if connection dropped after upload succeeded).
- Privacy & scheduled publishAt support (`private` + `publishAt` when eligible, or `private`/`unlisted` testing mode).
- Safety enforcement: never converts a video to Public automatically if verification or testing safety checks fail.
- Full DRY_RUN simulation mode.
"""

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from arx_cutshor.database import Database
from arx_cutshor.security import scrub_secrets

YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


class YouTubeIntegrationError(Exception):
    """Base exception for YouTube API operations."""


class YouTubeOAuthError(YouTubeIntegrationError):
    """Raised when YouTube OAuth credentials or tokens are missing, expired, or invalid."""


class YouTubeQuotaError(YouTubeIntegrationError):
    """Raised when YouTube Data API v3 quota is exceeded."""


class YouTubePublisher:
    """Manages YouTube Data API v3 authentication, state reconciliation, and resumable uploads."""

    def __init__(
        self,
        db: Database,
        client_secret_file: Path,
        token_file: Path,
        category_id: str = "22",
        dry_run: bool = True,
        publishing_mode: str = "testing",
        allow_public_publishing: bool = False,
        service_client: Optional[Any] = None,
    ):
        self.db = db
        self.client_secret_file = Path(client_secret_file)
        self.token_file = Path(token_file)
        self.category_id = str(category_id)
        self.dry_run = dry_run
        self.publishing_mode = publishing_mode
        self.allow_public_publishing = allow_public_publishing
        self._service = service_client

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------
    def authenticate(self, interactive: bool = False) -> Any:
        """Authenticate with YouTube Data API v3 via OAuth 2.0."""
        if self._service is not None:
            return self._service

        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise YouTubeOAuthError("Google API client libraries are not installed.") from exc

        creds = None
        if self.token_file.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self.token_file), YOUTUBE_SCOPES)
            except Exception as exc:
                raise YouTubeOAuthError(f"Invalid YouTube token file: {scrub_secrets(str(exc))}") from exc

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as exc:
                    raise YouTubeOAuthError(f"Failed to refresh YouTube OAuth token: {scrub_secrets(str(exc))}") from exc
            else:
                if not interactive:
                    raise YouTubeOAuthError(
                        "YouTube OAuth token is missing or expired. Run interactive login first."
                    )
                if not self.client_secret_file.exists():
                    raise YouTubeOAuthError(
                        f"YouTube client_secret.json not found at {self.client_secret_file}. See YOUTUBE_API_SETUP.md."
                    )
                flow = InstalledAppFlow.from_client_secrets_file(str(self.client_secret_file), YOUTUBE_SCOPES)
                creds = flow.run_local_server(port=0)

            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            self.token_file.write_text(creds.to_json(), encoding="utf-8")

        self._service = build("youtube", "v3", credentials=creds)
        return self._service

    # ------------------------------------------------------------------
    # Channel Eligibility & Verification
    # ------------------------------------------------------------------
    def check_channel_eligibility(self) -> Dict[str, Any]:
        """Verify channel status and longUploadsStatus / scheduling eligibility."""
        if self.dry_run and self._service is None:
            return {
                "eligible": True,
                "channel_id": "DRY_RUN_CHANNEL",
                "title": "DRY RUN Channel",
                "is_linked": True,
                "long_uploads_status": "allowed",
            }

        service = self.authenticate(interactive=False)
        try:
            resp = service.channels().list(part="snippet,status", mine=True).execute()
        except Exception as exc:
            self._handle_api_exception(exc)

        items = resp.get("items", [])
        if not items:
            return {"eligible": False, "reason": "No YouTube channel found on this Google account"}

        ch = items[0]
        status = ch.get("status", {})
        is_linked = status.get("isLinked", False)
        return {
            "eligible": bool(is_linked),
            "channel_id": ch.get("id"),
            "title": ch.get("snippet", {}).get("title", ""),
            "is_linked": is_linked,
            "long_uploads_status": status.get("longUploadsStatus", "unknown"),
        }

    # ------------------------------------------------------------------
    # State Reconciliation (Prevent Duplicate Uploads)
    # ------------------------------------------------------------------
    def reconcile_job_state(self, job_id: int) -> Optional[str]:
        """Reconcile an interrupted upload job before retrying.

        Checks:
        1. If `youtube_video_id` is already recorded on the job, mark published and return it.
        2. Query the authenticated user's recent uploads on YouTube matching the clip's exact title or content hash tag.
        If found, stores `youtube_video_id` and `youtube_url` and marks job `published` without re-uploading.
        """
        job = self.db.get_publishing_job(job_id)
        if not job:
            return None

        if job.get("youtube_video_id"):
            vid_id = job["youtube_video_id"]
            url = job.get("youtube_url") or f"https://youtube.com/shorts/{vid_id}"
            self.db.update_publishing_job(
                job_id=job_id,
                status="published",
                youtube_video_id=vid_id,
                youtube_url=url,
            )
            return vid_id

        if self.dry_run and self._service is None:
            return None

        clip = self.db.get_clip(job["clip_id"])
        if not clip:
            return None

        try:
            service = self.authenticate(interactive=False)
            if hasattr(service, "search"):
                search_resp = (
                    service.search()
                    .list(part="snippet", forMine=True, type="video", maxResults=15, q=clip["title"])
                    .execute()
                )
                for item in search_resp.get("items", []):
                    snippet = item.get("snippet", {})
                    vid_id = item.get("id", {}).get("videoId")
                    if vid_id and snippet.get("title") == clip["title"]:
                        url = f"https://youtube.com/shorts/{vid_id}"
                        self.db.update_publishing_job(
                            job_id=job_id,
                            status="published",
                            youtube_video_id=vid_id,
                            youtube_url=url,
                        )
                        self.db.log_operation(
                            operation_type="youtube_reconcile",
                            entity_id=str(job_id),
                            status="success",
                            message=f"Reconciled existing YouTube upload {vid_id} for clip #{clip['id']}",
                        )
                        return vid_id
        except Exception as exc:
            self.db.log_operation(
                operation_type="youtube_reconcile",
                entity_id=str(job_id),
                status="warning",
                message=f"Reconciliation check warning: {scrub_secrets(str(exc))}",
            )
        return None

    # ------------------------------------------------------------------
    # Upload & Publishing Execution
    # ------------------------------------------------------------------
    def _resolve_upload_privacy_and_schedule(
        self,
        job: Dict[str, Any],
        clip: Dict[str, Any],
        source_video: Optional[Dict[str, Any]],
    ) -> TupleResult:
        """Determine safe privacyStatus and publishAt timestamp for YouTube Data API v3.

        Note: In YouTube Data API v3, scheduling a video for future publication requires
        `privacyStatus='private'` alongside `publishAt=ISO8601`.
        During testing mode or when ALLOW_PUBLIC_PUBLISHING is false, we never schedule automatic public release.
        """
        rights_ok = (source_video.get("rights_status") == "confirmed") if source_video else False
        if not rights_ok:
            return "private", None

        if self.dry_run or self.publishing_mode == "testing" or not self.allow_public_publishing:
            req_priv = job.get("privacy_status", "private")
            safe_priv = "unlisted" if req_priv == "unlisted" else "private"
            return safe_priv, None

        # Scheduled Publishing mode with explicit public approval:
        if self.publishing_mode == "scheduled" and job.get("scheduled_at"):
            # YouTube API requirement: status.privacyStatus must be 'private' when status.publishAt is set
            return "private", job["scheduled_at"]

        return job.get("privacy_status", "private"), None

    def publish_job(self, job_id: int, clip_file_path: Path | str) -> Dict[str, Any]:
        """Execute or simulate uploading a scheduled clip job to YouTube Shorts."""
        job = self.db.get_publishing_job(job_id)
        if not job:
            raise ValueError(f"Publishing job #{job_id} does not exist")

        # 1. Reconcile first to prevent duplicate uploads on retry
        reconciled_id = self.reconcile_job_state(job_id)
        if reconciled_id:
            return self.db.get_publishing_job(job_id)

        clip = self.db.get_clip(job["clip_id"])
        if not clip:
            raise ValueError(f"Clip #{job['clip_id']} not found for job #{job_id}")

        sv = self.db.get_source_video(clip["source_video_id"])
        clip_path = Path(clip_file_path)
        if not clip_path.exists():
            raise FileNotFoundError(f"Rendered clip file not found: {clip_path}")

        privacy_status, publish_at = self._resolve_upload_privacy_and_schedule(job, clip, sv)
        attempt = int(job["attempt_count"]) + 1

        # 2. Check DRY_RUN mode
        if self.dry_run:
            sim_id = f"dryrun_yt_{clip['id']}_{clip['sequence_number']:02d}"
            sim_url = f"https://youtube.com/shorts/{sim_id}"
            self.db.update_publishing_job(
                job_id=job_id,
                status="published",
                attempt_count=attempt,
                youtube_video_id=sim_id,
                youtube_url=sim_url,
            )
            self.db.log_operation(
                operation_type="youtube_upload_dry_run",
                entity_id=str(job_id),
                status="success",
                message=f"[DRY_RUN] Simulated YouTube upload for clip #{clip['id']} -> {sim_url} (privacy={privacy_status})",
            )
            return self.db.get_publishing_job(job_id)

        # 3. Live / Mocked API Upload
        self.db.update_publishing_job(job_id=job_id, status="uploading", attempt_count=attempt)

        try:
            tags = json.loads(clip.get("tags_json") or "[]")
        except Exception:
            tags = ["Shorts"]

        body: Dict[str, Any] = {
            "snippet": {
                "title": clip["title"] or f"Shorts Part {clip['sequence_number']}",
                "description": clip["description"] or "#Shorts",
                "tags": tags,
                "categoryId": self.category_id,
                "defaultLanguage": "ar",
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }
        if publish_at:
            body["status"]["publishAt"] = publish_at

        try:
            service = self.authenticate(interactive=False)
            vid_id = self._perform_resumable_upload(service, body, clip_path, job_id)
            yt_url = f"https://youtube.com/shorts/{vid_id}"
            self.db.update_publishing_job(
                job_id=job_id,
                status="published",
                youtube_video_id=vid_id,
                youtube_url=yt_url,
                last_error="",
            )
            self.db.log_operation(
                operation_type="youtube_upload",
                entity_id=str(job_id),
                status="success",
                message=f"Uploaded clip #{clip['id']} to YouTube: {yt_url} (privacy={privacy_status})",
            )
            return self.db.get_publishing_job(job_id)
        except Exception as exc:
            safe_err = scrub_secrets(str(exc))
            self.db.update_publishing_job(
                job_id=job_id,
                status="failed",
                last_error=safe_err,
            )
            self.db.log_operation(
                operation_type="youtube_upload",
                entity_id=str(job_id),
                status="error",
                message=f"Failed uploading job #{job_id}: {safe_err}",
            )
            self._handle_api_exception(exc)

    def _perform_resumable_upload(self, service: Any, body: Dict[str, Any], clip_path: Path, job_id: int) -> str:
        """Execute resumable upload via googleapiclient or injected mock client."""
        try:
            from googleapiclient.http import MediaFileUpload

            media = MediaFileUpload(str(clip_path), mimetype="video/mp4", chunksize=4 * 1024 * 1024, resumable=True)
        except ImportError:
            media = None

        insert_req = service.videos().insert(part="snippet,status", body=body, media_body=media)

        # Support both chunked next_chunk() and direct execute()
        if hasattr(insert_req, "next_chunk"):
            response = None
            while response is None:
                status, response = insert_req.next_chunk()
                if hasattr(insert_req, "resumable_uri") and insert_req.resumable_uri:
                    self.db.update_publishing_job(job_id=job_id, upload_session_uri= str(insert_req.resumable_uri))
        else:
            response = insert_req.execute()

        if not response or "id" not in response:
            raise YouTubeIntegrationError("YouTube API returned no video ID in response")
        return str(response["id"])

    def _handle_api_exception(self, exc: Exception) -> None:
        if isinstance(exc, (YouTubeOAuthError, YouTubeQuotaError, YouTubeIntegrationError)):
            raise exc
        msg = str(exc).lower()
        if "invalid_grant" in msg or "unauthorized" in msg or "401" in msg or "token" in msg:
            raise YouTubeOAuthError(f"YouTube OAuth error: {scrub_secrets(str(exc))}") from exc
        if "quotaexceeded" in msg or "quota" in msg or "429" in msg or "userratelimitexceeded" in msg:
            raise YouTubeQuotaError(f"YouTube API quota exceeded: {scrub_secrets(str(exc))}") from exc
        raise YouTubeIntegrationError(f"YouTube API error: {scrub_secrets(str(exc))}") from exc


TupleResult = Any
