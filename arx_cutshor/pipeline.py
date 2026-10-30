"""End-to-End Orchestration Pipeline for ARX-cutShor.

Coordinates:
1. Discovering new videos from Google Drive (or local input folder).
2. Downloading and verifying source video integrity.
3. Splitting into YouTube Shorts segments and applying the branding template.
4. Generating Arabic/RTL publishing metadata (Title, Description, Hashtags, Tags).
5. Creating clip records and scheduling publishing jobs without duplicates.
6. Executing due publishing jobs (supporting DRY_RUN, Private/Unlisted Testing, and Scheduled Publishing).
7. Generating execution reports and cleaning temporary workspace files.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from arx_cutshor.config import AppConfig
from arx_cutshor.database import Database
from arx_cutshor.gdrive import GoogleDriveManager
from arx_cutshor.metadata import MetadataGenerator
from arx_cutshor.notifications import NotificationService
from arx_cutshor.scheduler import SchedulerEngine
from arx_cutshor.security import cleanup_temp_files, scrub_secrets, setup_logger
from arx_cutshor.video_engine import VideoEngine
from arx_cutshor.youtube import YouTubePublisher


class PipelineOrchestrator:
    """High-level coordinator uniting all decoupled subsystems."""

    def __init__(
        self,
        config: AppConfig,
        db: Optional[Database] = None,
        video_engine: Optional[VideoEngine] = None,
        gdrive: Optional[GoogleDriveManager] = None,
        metadata_gen: Optional[MetadataGenerator] = None,
        scheduler: Optional[SchedulerEngine] = None,
        youtube: Optional[YouTubePublisher] = None,
        notifier: Optional[NotificationService] = None,
    ):
        self.config = config
        self.config.ensure_directories()
        self.logger = setup_logger("arx_cutshor", self.config.logs_dir)
        self.db = db or Database(self.config.database_path)
        self.video_engine = video_engine or VideoEngine(
            ffmpeg_path=self.config.ffmpeg_path,
            ffprobe_path=self.config.ffprobe_path,
        )
        self.gdrive = gdrive or GoogleDriveManager(
            db=self.db,
            workspace_dir=self.config.workspace_dir,
            client_secret_file=self.config.gdrive_client_secret_file,
            token_file=self.config.gdrive_token_file,
            input_folder_id=self.config.gdrive_input_folder_id,
            dry_run=self.config.dry_run,
        )
        self.metadata_gen = metadata_gen or MetadataGenerator(
            channel_name=self.config.channel_name,
            default_series_name=self.config.default_series_name,
            default_language=self.config.default_language,
        )
        self.scheduler = scheduler or SchedulerEngine(
            db=self.db,
            timezone_name=self.config.timezone,
            daily_clip_limit=self.config.daily_clip_limit,
            allowed_days=self.config.allowed_publish_days,
            publish_hours=self.config.publish_hours,
            min_interval_minutes=self.config.min_interval_minutes,
            max_retry_attempts=self.config.max_retry_attempts,
            publishing_mode=self.config.publishing_mode,
            allow_public_publishing=self.config.allow_public_publishing,
            dry_run=self.config.dry_run,
        )
        self.youtube = youtube or YouTubePublisher(
            db=self.db,
            client_secret_file=self.config.youtube_client_secret_file,
            token_file=self.config.youtube_token_file,
            category_id=self.config.youtube_category_id,
            dry_run=self.config.dry_run,
            publishing_mode=self.config.publishing_mode,
            allow_public_publishing=self.config.allow_public_publishing,
        )
        self.notifier = notifier or NotificationService(
            db=self.db,
            webhook_url=self.config.notification_webhook_url,
            telegram_bot_token=self.config.telegram_bot_token,
            telegram_chat_id=self.config.telegram_chat_id,
            dry_run=self.config.dry_run,
        )

    # ------------------------------------------------------------------
    # Single Video Processing Flow
    # ------------------------------------------------------------------
    def process_source_video(
        self,
        video_id: int,
        template_name: str = "default",
        force_reprocess: bool = False,
    ) -> Dict[str, Any]:
        """Process a registered source video from download/validation to ready clips and metadata."""
        if force_reprocess:
            self.db.reset_source_video_for_reprocessing(video_id)

        sv = self.db.get_source_video(video_id)
        if not sv:
            raise ValueError(f"Source video #{video_id} not found in database")

        if sv["status"] in ("ready", "completed", "published") and not force_reprocess:
            existing_clips = self.db.list_clips(source_video_id=video_id)
            return {"status": "skipped_already_processed", "video": sv, "clips": existing_clips}

        self.db.update_source_video(video_id, status="processing", error_message="")

        try:
            # 1. Resolve local file or download from Google Drive
            local_path_str = sv.get("local_path") or ""
            if local_path_str and Path(local_path_str).exists():
                local_path = Path(local_path_str)
            else:
                local_path = self.gdrive.download_video(sv)

            # 2. Validate & probe metadata
            meta = self.video_engine.probe_video(local_path)
            self.db.update_source_video(video_id, duration_seconds=meta.duration_seconds, local_path=str(local_path))

            # 3. Load branding template
            template = self.db.get_template(template_name)

            # 4. Split and render clips into workspace/ready
            ready_dir = self.config.workspace_dir / "ready"
            _, segments = self.video_engine.process_video_to_clips(
                source_path=local_path,
                output_dir=ready_dir,
                mode=self.config.split_mode,
                target_duration=self.config.target_clip_duration_sec,
                min_duration=self.config.min_clip_duration_sec,
                max_duration=self.config.max_clip_duration_sec,
                safe_end_margin=self.config.safe_end_margin_sec,
                template=template,
            )

            created_clips: List[Dict[str, Any]] = []
            for seg in segments:
                pub_meta = self.metadata_gen.generate(
                    source_filename=sv["filename"],
                    part_number=seg.sequence_number,
                    total_parts=seg.total_parts,
                    series_name=sv.get("series_name") or None,
                )
                clip_rec = self.db.create_clip(
                    source_video_id=video_id,
                    sequence_number=seg.sequence_number,
                    total_parts=seg.total_parts,
                    start_seconds=seg.start_seconds,
                    end_seconds=seg.end_seconds,
                    output_filename=str(seg.output_path),
                    duration_seconds=seg.duration_seconds,
                    content_hash=seg.content_hash,
                    status="ready",
                    title=pub_meta.title,
                    description=pub_meta.description,
                    hashtags=pub_meta.hashtags_str,
                    tags_json=json.dumps(pub_meta.tags, ensure_ascii=False),
                )
                created_clips.append(clip_rec)

            self.db.update_source_video(video_id, status="ready", processed=True)
            self.db.log_operation(
                operation_type="process_video",
                entity_id=str(video_id),
                status="success",
                message=f"Processed '{sv['filename']}' into {len(created_clips)} clips",
            )
            return {"status": "success", "video": self.db.get_source_video(video_id), "clips": created_clips}

        except Exception as exc:
            err_msg = scrub_secrets(str(exc))
            self.db.update_source_video(video_id, status="failed", error_message=err_msg)
            self.db.log_operation(
                operation_type="process_video",
                entity_id=str(video_id),
                status="error",
                message=f"Failed processing video #{video_id}: {err_msg}",
            )
            self.notifier.notify("فشل معالجة فيديو", f"الفيديو #{video_id}: {err_msg}", level="error")
            raise

    # ------------------------------------------------------------------
    # Full Pipeline Cycle & Report Generation
    # ------------------------------------------------------------------
    def run_cycle(self, max_videos: int = 2, execute_due_uploads: bool = True) -> Dict[str, Any]:
        """Run one automated pipeline cycle:
        1. Recover any interrupted operations.
        2. Discover new videos from Google Drive (if enabled).
        3. Process up to `max_videos` discovered videos.
        4. Schedule all ready clips.
        5. Execute due publishing jobs (or simulate in DRY_RUN).
        6. Clean up expired temp files and write JSON report.
        """
        recovery = self.db.recover_interrupted_operations()
        discovered_count = 0

        if self.config.gdrive_enabled and self.config.gdrive_input_folder_id:
            new_vids = self.gdrive.discover_new_videos()
            discovered_count = len(new_vids)

        pending_videos = self.db.list_source_videos(status="discovered")[:max_videos]
        processed_results = []
        for sv in pending_videos:
            try:
                res = self.process_source_video(sv["id"])
                processed_results.append(res)
            except Exception:
                continue

        scheduled_jobs = self.scheduler.schedule_ready_clips(
            requested_privacy=self.config.get_effective_privacy()
        )

        published_jobs = []
        if execute_due_uploads:
            due = self.scheduler.get_due_jobs()
            for job in due:
                try:
                    updated = self.youtube.publish_job(job["id"], job["output_filename"])
                    published_jobs.append(updated)
                except Exception:
                    continue

        cleaned_files = 0
        if self.config.auto_cleanup_temp:
            cleaned_files = cleanup_temp_files(
                self.config.workspace_dir / "temp",
                max_age_seconds=3600,
                max_total_mb=self.config.max_temp_storage_mb,
            )

        report = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "dry_run": self.config.dry_run,
            "publishing_mode": self.config.publishing_mode,
            "recovery": recovery,
            "newly_discovered": discovered_count,
            "processed_videos": len(processed_results),
            "newly_scheduled_jobs": len(scheduled_jobs),
            "executed_publishing_jobs": len(published_jobs),
            "temp_files_cleaned": cleaned_files,
            "dashboard_stats": self.db.get_dashboard_stats(),
        }

        report_file = (
            self.config.reports_dir
            / f"report_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
        )
        report_file.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        report["report_path"] = str(report_file)
        return report

