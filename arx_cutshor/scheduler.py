"""Publishing Scheduler Engine for ARX-cutShor.

Independent of the video processing engine.
Features:
- Daily clip limit, allowed days of week, publishing hours, timezone support.
- Minimum interval between clips and strict collision prevention.
- Preserves original source clip sequence order.
- Pause and resume schedule support.
- Rescheduling failed clips with attempt caps.
- Respects YouTube API daily quota limits (default 10,000 units/day; 1 upload = 1,600 units -> max 6 uploads/day on free quota).
- Enforces Private/Unlisted Testing vs Scheduled Publishing modes, blocking public publishing during testing or when rights are unconfirmed.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from arx_cutshor.database import Database

# YouTube Data API v3 default quota: 10,000 units/day; videos.insert costs ~1,600 units
YOUTUBE_FREE_DAILY_UPLOAD_CAP = 6


class SchedulerEngine:
    """Calculates and manages publishing schedules for processed clips."""

    def __init__(
        self,
        db: Database,
        timezone_name: str = "Asia/Riyadh",
        daily_clip_limit: int = 3,
        allowed_days: Optional[List[int]] = None,
        publish_hours: Optional[List[str]] = None,
        min_interval_minutes: int = 120,
        max_retry_attempts: int = 3,
        publishing_mode: str = "testing",
        allow_public_publishing: bool = False,
        dry_run: bool = True,
    ):
        self.db = db
        self.timezone_name = timezone_name
        try:
            self.tz = ZoneInfo(timezone_name)
        except Exception:
            self.tz = timezone.utc
            self.timezone_name = "UTC"

        # Respect YouTube API free daily upload quota limit
        self.daily_clip_limit = max(1, min(int(daily_clip_limit), YOUTUBE_FREE_DAILY_UPLOAD_CAP))
        self.allowed_days = allowed_days if allowed_days is not None else [0, 1, 2, 3, 4, 5, 6]
        self.publish_hours = sorted(publish_hours if publish_hours else ["14:00", "18:00", "21:00"])
        self.min_interval_minutes = max(15, int(min_interval_minutes))
        self.max_retry_attempts = max(1, int(max_retry_attempts))
        self.publishing_mode = publishing_mode
        self.allow_public_publishing = allow_public_publishing
        self.dry_run = dry_run

    # ------------------------------------------------------------------
    # Pause / Resume Controls
    # ------------------------------------------------------------------
    def is_paused(self) -> bool:
        val = self.db.get_setting("scheduler_paused", "false")
        return str(val).lower() in ("true", "1", "yes")

    def pause(self) -> None:
        self.db.set_setting("scheduler_paused", "true")
        self.db.log_operation("scheduler", "paused", "Publishing schedule paused by administrator")

    def resume(self) -> None:
        self.db.set_setting("scheduler_paused", "false")
        self.db.log_operation("scheduler", "resumed", "Publishing schedule resumed")

    # ------------------------------------------------------------------
    # Privacy & Safety Gates
    # ------------------------------------------------------------------
    def resolve_safe_privacy(self, requested_privacy: str = "private", rights_status: str = "confirmed") -> str:
        """Enforce safety and copyright rules:
        - Never publish publicly during testing or DRY_RUN.
        - Never publish publicly without explicit ALLOW_PUBLIC_PUBLISHING=true.
        - If copyright/rights status is unconfirmed, hold as private.
        """
        if rights_status != "confirmed":
            return "private"
        if self.dry_run or self.publishing_mode == "testing" or not self.allow_public_publishing:
            return "unlisted" if requested_privacy == "unlisted" else "private"
        return requested_privacy

    # ------------------------------------------------------------------
    # Slot Calculation Engine
    # ------------------------------------------------------------------
    def _parse_hour_minute(self, hm_str: str) -> tuple[int, int]:
        parts = hm_str.strip().split(":")
        return int(parts[0]), int(parts[1]) if len(parts) > 1 else 0

    def get_existing_scheduled_times(self) -> List[datetime]:
        jobs = self.db.list_publishing_jobs()
        times: List[datetime] = []
        for j in jobs:
            if j["status"] in ("scheduled", "uploading", "published"):
                try:
                    dt = datetime.fromisoformat(j["scheduled_at"].replace("Z", "+00:00"))
                    times.append(dt.astimezone(self.tz))
                except Exception:
                    continue
        return sorted(times)

    def calculate_next_slots(self, count: int, reference_time: Optional[datetime] = None) -> List[datetime]:
        """Calculate `count` valid future publishing slots respecting:
        - allowed weekdays (0=Monday .. 6=Sunday)
        - configured publish hours
        - daily clip limit & YouTube API quota cap
        - minimum interval between any two clips
        - zero collision with existing scheduled jobs
        """
        if count <= 0:
            return []

        now_local = (reference_time or datetime.now(timezone.utc)).astimezone(self.tz)
        booked = self.get_existing_scheduled_times()
        allocated: List[datetime] = []

        day_offset = 0
        max_days_search = 365

        while len(allocated) < count and day_offset < max_days_search:
            candidate_date = (now_local + timedelta(days=day_offset)).date()
            day_offset += 1

            if candidate_date.weekday() not in self.allowed_days:
                continue

            # Count how many clips are already scheduled on this date
            all_known = booked + allocated
            day_count = sum(1 for dt in all_known if dt.date() == candidate_date)
            if day_count >= self.daily_clip_limit:
                continue

            for hm in self.publish_hours:
                if len(allocated) >= count or day_count >= self.daily_clip_limit:
                    break

                hour, minute = self._parse_hour_minute(hm)
                slot_dt = datetime(
                    candidate_date.year,
                    candidate_date.month,
                    candidate_date.day,
                    hour,
                    minute,
                    0,
                    tzinfo=self.tz,
                )

                if slot_dt <= now_local + timedelta(minutes=2):
                    continue

                # Check minimum interval against all booked and newly allocated slots
                conflict = False
                for existing_dt in booked + allocated:
                    diff_minutes = abs((slot_dt - existing_dt).total_seconds()) / 60.0
                    if diff_minutes < self.min_interval_minutes:
                        conflict = True
                        break

                if not conflict:
                    allocated.append(slot_dt)
                    day_count += 1

        return allocated

    # ------------------------------------------------------------------
    # Scheduling Ready Clips
    # ------------------------------------------------------------------
    def schedule_ready_clips(
        self,
        source_video_id: Optional[int] = None,
        requested_privacy: str = "private",
        reference_time: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Schedule all ready clips in strict sequence order without duplicating jobs."""
        ready_clips = self.db.list_clips(source_video_id=source_video_id, status="ready")
        # Sort by source_video_id then sequence_number to strictly preserve part order
        ready_clips.sort(key=lambda c: (c["source_video_id"], c["sequence_number"]))

        unscheduled_clips: List[Dict[str, Any]] = []
        for clip in ready_clips:
            existing_job = self.db.get_publishing_job_by_clip(clip["id"])
            if not existing_job:
                unscheduled_clips.append(clip)

        if not unscheduled_clips:
            return []

        slots = self.calculate_next_slots(len(unscheduled_clips), reference_time=reference_time)
        created_jobs: List[Dict[str, Any]] = []

        for clip, slot_dt in zip(unscheduled_clips, slots):
            sv = self.db.get_source_video(clip["source_video_id"])
            rights_status = sv["rights_status"] if sv else "unconfirmed"
            safe_privacy = self.resolve_safe_privacy(requested_privacy, rights_status=rights_status)

            # If rights are unconfirmed, require review before scheduling for public
            slot_utc_iso = slot_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            job, created = self.db.create_publishing_job(
                clip_id=clip["id"],
                scheduled_at=slot_utc_iso,
                timezone_str=self.timezone_name,
                privacy_status=safe_privacy,
            )
            if rights_status != "confirmed":
                self.db.update_publishing_job(
                    job["id"],
                    status="needs_review",
                    last_error="Rights status is unconfirmed; manual review required before publishing.",
                )
                job = self.db.get_publishing_job(job["id"])

            if created and job:
                self.db.log_operation(
                    operation_type="schedule_clip",
                    entity_id=str(clip["id"]),
                    status="success",
                    message=f"Scheduled clip #{clip['id']} (part {clip['sequence_number']}) at {slot_utc_iso} [{safe_privacy}]",
                )
                created_jobs.append(job)

        return created_jobs

    # ------------------------------------------------------------------
    # Rescheduling Failed Jobs
    # ------------------------------------------------------------------
    def reschedule_failed_job(self, job_id: int, reference_time: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
        """Reschedule a failed job if it hasn't exceeded max_retry_attempts."""
        job = self.db.get_publishing_job(job_id)
        if not job:
            raise ValueError(f"Publishing job {job_id} not found")

        if job["status"] == "published" or job.get("youtube_video_id"):
            # Never re-schedule an already published clip
            return job

        if int(job["attempt_count"]) >= self.max_retry_attempts:
            self.db.log_operation(
                operation_type="reschedule_job",
                entity_id=str(job_id),
                status="skipped",
                message=f"Job #{job_id} reached max retry attempts ({self.max_retry_attempts})",
            )
            return None

        slots = self.calculate_next_slots(1, reference_time=reference_time)
        if not slots:
            return None

        new_slot_iso = slots[0].astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.db.update_publishing_job(
            job_id=job_id,
            status="scheduled",
            scheduled_at=new_slot_iso,
        )
        self.db.log_operation(
            operation_type="reschedule_job",
            entity_id=str(job_id),
            status="success",
            message=f"Rescheduled failed job #{job_id} to {new_slot_iso}",
        )
        return self.db.get_publishing_job(job_id)

    def get_due_jobs(self, current_time: Optional[datetime] = None) -> List[Dict[str, Any]]:
        """Return all scheduled jobs whose scheduled_at <= current_time, unless scheduler is paused."""
        if self.is_paused():
            return []

        now_utc = (current_time or datetime.now(timezone.utc)).astimezone(timezone.utc)
        now_iso = now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        scheduled = self.db.list_publishing_jobs(status="scheduled")
        return [j for j in scheduled if j["scheduled_at"] <= now_iso]
