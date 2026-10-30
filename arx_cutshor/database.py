"""SQLite Database Management Module for ARX-cutShor.

Implements all required tables, foreign keys, uniqueness constraints,
transactional operations, backup/restore, and state reconciliation.
"""

import json
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from arx_cutshor.security import scrub_secrets

TupleResult = Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS source_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    drive_file_id TEXT UNIQUE NOT NULL,
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    duration_seconds REAL DEFAULT 0.0,
    status TEXT NOT NULL DEFAULT 'discovered',
    rights_status TEXT NOT NULL DEFAULT 'confirmed',
    checksum TEXT DEFAULT '',
    local_path TEXT DEFAULT '',
    series_name TEXT DEFAULT '',
    discovered_at TEXT NOT NULL,
    processed_at TEXT DEFAULT NULL,
    error_message TEXT DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_video_id INTEGER NOT NULL,
    sequence_number INTEGER NOT NULL,
    total_parts INTEGER NOT NULL DEFAULT 1,
    start_seconds REAL NOT NULL,
    end_seconds REAL NOT NULL,
    output_filename TEXT NOT NULL,
    duration_seconds REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'ready',
    content_hash TEXT NOT NULL,
    title TEXT DEFAULT '',
    description TEXT DEFAULT '',
    hashtags TEXT DEFAULT '',
    tags_json TEXT DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY (source_video_id) REFERENCES source_videos(id) ON DELETE CASCADE,
    UNIQUE(source_video_id, sequence_number)
);

CREATE TABLE IF NOT EXISTS publishing_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER NOT NULL UNIQUE,
    scheduled_at TEXT NOT NULL,
    timezone TEXT NOT NULL DEFAULT 'Asia/Riyadh',
    privacy_status TEXT NOT NULL DEFAULT 'private',
    status TEXT NOT NULL DEFAULT 'scheduled',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    upload_session_uri TEXT DEFAULT NULL,
    youtube_video_id TEXT DEFAULT NULL,
    youtube_url TEXT DEFAULT NULL,
    last_error TEXT DEFAULT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (clip_id) REFERENCES clips(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    configuration_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operation_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_type TEXT NOT NULL,
    entity_id TEXT DEFAULT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_source_videos_status ON source_videos(status);
CREATE INDEX IF NOT EXISTS idx_clips_source_id ON clips(source_video_id);
CREATE INDEX IF NOT EXISTS idx_publishing_jobs_status ON publishing_jobs(status);
CREATE INDEX IF NOT EXISTS idx_publishing_jobs_scheduled_at ON publishing_jobs(scheduled_at);
"""


DEFAULT_TEMPLATE_CONFIG = {
    "enabled": True,
    "logo_enabled": False,
    "logo_path": "",
    "logo_position": "top-right",
    "logo_scale": 0.15,
    "logo_opacity": 0.9,
    "intro_enabled": False,
    "intro_path": "",
    "outro_enabled": False,
    "outro_path": "",
    "opening_text_enabled": True,
    "opening_text": "",
    "opening_text_duration": 3.0,
    "subscribe_text_enabled": True,
    "subscribe_text": "اشترك بالقناة للمزيد",
    "subscribe_text_start": 2.0,
    "subscribe_text_duration": 4.0,
    "like_text_enabled": True,
    "like_text": "لا تنسَ الإعجاب بالمقطع",
    "like_text_start": 6.0,
    "like_text_duration": 4.0,
    "part_badge_enabled": True,
    "font_color": "white",
    "box_color": "black@0.55",
    "accent_color": "#FF0055",
    "font_size": 42,
    "target_width": 1080,
    "target_height": 1920,
    "background_mode": "pad_black",
}


class Database:
    """Thread-safe transactional SQLite manager for ARX-cutShor."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    @contextmanager
    def transaction(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self) -> None:
        with self.transaction() as conn:
            conn.executescript(SCHEMA_SQL)
            now = _utc_now_iso()
            # Seed default template if not present
            cur = conn.execute("SELECT id FROM templates WHERE name = ?", ("default",))
            if not cur.fetchone():
                conn.execute(
                    "INSERT INTO templates (name, configuration_json, created_at, updated_at) VALUES (?, ?, ?, ?)",
                    ("default", json.dumps(DEFAULT_TEMPLATE_CONFIG, ensure_ascii=False), now, now),
                )
            # Seed default scheduler state if not present
            defaults = {
                "scheduler_paused": "false",
                "publishing_mode": "testing",
                "daily_clip_limit": "3",
                "timezone": "Asia/Riyadh",
                "split_mode": "fixed",
                "target_clip_duration_sec": "55.0",
            }
            for k, v in defaults.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                    (k, v, now),
                )

    # ------------------------------------------------------------------
    # Operation Logs
    # ------------------------------------------------------------------
    def log_operation(
        self,
        operation_type: str,
        status: str,
        message: str,
        entity_id: Optional[str] = None,
    ) -> int:
        safe_message = scrub_secrets(message)
        now = _utc_now_iso()
        with self.transaction() as conn:
            cur = conn.execute(
                "INSERT INTO operation_logs (operation_type, entity_id, status, message, created_at) VALUES (?, ?, ?, ?, ?)",
                (operation_type, str(entity_id) if entity_id is not None else None, status, safe_message, now),
            )
            return int(cur.lastrowid)

    def get_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self.transaction() as conn:
            rows = conn.execute(
                "SELECT * FROM operation_logs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Source Videos
    # ------------------------------------------------------------------
    def register_source_video(
        self,
        drive_file_id: str,
        filename: str,
        mime_type: str,
        size_bytes: int,
        rights_status: str = "confirmed",
        checksum: str = "",
        local_path: str = "",
        series_name: str = "",
    ) -> TupleResult:
        """Register a discovered video if not already registered.

        Returns (video_dict, is_new).
        """
        now = _utc_now_iso()
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM source_videos WHERE drive_file_id = ?", (drive_file_id,)
            ).fetchone()
            if existing:
                return dict(existing), False

            cur = conn.execute(
                """
                INSERT INTO source_videos (
                    drive_file_id, filename, mime_type, size_bytes,
                    status, rights_status, checksum, local_path, series_name, discovered_at
                ) VALUES (?, ?, ?, ?, 'discovered', ?, ?, ?, ?, ?)
                """,
                (
                    drive_file_id,
                    filename,
                    mime_type,
                    size_bytes,
                    rights_status,
                    checksum,
                    local_path,
                    series_name,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM source_videos WHERE id = ?", (cur.lastrowid,)).fetchone()
            return dict(row), True

    def get_source_video(self, video_id: int) -> Optional[Dict[str, Any]]:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM source_videos WHERE id = ?", (video_id,)).fetchone()
            return dict(row) if row else None

    def get_source_video_by_drive_id(self, drive_file_id: str) -> Optional[Dict[str, Any]]:
        with self.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM source_videos WHERE drive_file_id = ?", (drive_file_id,)
            ).fetchone()
            return dict(row) if row else None

    def list_source_videos(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.transaction() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM source_videos WHERE status = ? ORDER BY id DESC", (status,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM source_videos ORDER BY id DESC").fetchall()
            return [dict(r) for r in rows]

    def update_source_video(
        self,
        video_id: int,
        status: Optional[str] = None,
        duration_seconds: Optional[float] = None,
        local_path: Optional[str] = None,
        rights_status: Optional[str] = None,
        error_message: Optional[str] = None,
        processed: bool = False,
    ) -> None:
        fields = []
        params: List[Any] = []
        if status is not None:
            fields.append("status = ?")
            params.append(status)
        if duration_seconds is not None:
            fields.append("duration_seconds = ?")
            params.append(duration_seconds)
        if local_path is not None:
            fields.append("local_path = ?")
            params.append(local_path)
        if rights_status is not None:
            fields.append("rights_status = ?")
            params.append(rights_status)
        if error_message is not None:
            fields.append("error_message = ?")
            params.append(scrub_secrets(error_message))
        if processed:
            fields.append("processed_at = ?")
            params.append(_utc_now_iso())

        if not fields:
            return
        params.append(video_id)
        with self.transaction() as conn:
            conn.execute(f"UPDATE source_videos SET {', '.join(fields)} WHERE id = ?", params)

    def reset_source_video_for_reprocessing(self, video_id: int) -> None:
        """Allow explicit reprocessing of a specific source video without duplicating published clips."""
        with self.transaction() as conn:
            conn.execute(
                "DELETE FROM clips WHERE source_video_id = ? AND status != 'published'",
                (video_id,),
            )
            conn.execute(
                "UPDATE source_videos SET status = 'discovered', error_message = NULL WHERE id = ?",
                (video_id,),
            )

    # ------------------------------------------------------------------
    # Clips
    # ------------------------------------------------------------------
    def create_clip(
        self,
        source_video_id: int,
        sequence_number: int,
        total_parts: int,
        start_seconds: float,
        end_seconds: float,
        output_filename: str,
        duration_seconds: float,
        content_hash: str,
        status: str = "ready",
        title: str = "",
        description: str = "",
        hashtags: str = "",
        tags_json: str = "[]",
    ) -> Dict[str, Any]:
        now = _utc_now_iso()
        with self.transaction() as conn:
            cur = conn.execute(
                """
                INSERT INTO clips (
                    source_video_id, sequence_number, total_parts,
                    start_seconds, end_seconds, output_filename,
                    duration_seconds, status, content_hash,
                    title, description, hashtags, tags_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_video_id, sequence_number) DO UPDATE SET
                    total_parts = excluded.total_parts,
                    start_seconds = excluded.start_seconds,
                    end_seconds = excluded.end_seconds,
                    output_filename = excluded.output_filename,
                    duration_seconds = excluded.duration_seconds,
                    status = excluded.status,
                    content_hash = excluded.content_hash,
                    title = excluded.title,
                    description = excluded.description,
                    hashtags = excluded.hashtags,
                    tags_json = excluded.tags_json
                """,
                (
                    source_video_id,
                    sequence_number,
                    total_parts,
                    start_seconds,
                    end_seconds,
                    output_filename,
                    duration_seconds,
                    status,
                    content_hash,
                    title,
                    description,
                    hashtags,
                    tags_json,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM clips WHERE source_video_id = ? AND sequence_number = ?",
                (source_video_id, sequence_number),
            ).fetchone()
            return dict(row)

    def get_clip(self, clip_id: int) -> Optional[Dict[str, Any]]:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM clips WHERE id = ?", (clip_id,)).fetchone()
            return dict(row) if row else None

    def list_clips(self, source_video_id: Optional[int] = None, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.transaction() as conn:
            query = "SELECT * FROM clips WHERE 1=1"
            params: List[Any] = []
            if source_video_id is not None:
                query += " AND source_video_id = ?"
                params.append(source_video_id)
            if status is not None:
                query += " AND status = ?"
                params.append(status)
            query += " ORDER BY source_video_id ASC, sequence_number ASC"
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def update_clip_metadata(
        self,
        clip_id: int,
        title: str,
        description: str,
        hashtags: str,
        tags: List[str],
    ) -> None:
        with self.transaction() as conn:
            conn.execute(
                "UPDATE clips SET title = ?, description = ?, hashtags = ?, tags_json = ? WHERE id = ?",
                (title, description, hashtags, json.dumps(tags, ensure_ascii=False), clip_id),
            )

    def update_clip_status(self, clip_id: int, status: str) -> None:
        with self.transaction() as conn:
            conn.execute("UPDATE clips SET status = ? WHERE id = ?", (status, clip_id))

    # ------------------------------------------------------------------
    # Publishing Jobs
    # ------------------------------------------------------------------
    def create_publishing_job(
        self,
        clip_id: int,
        scheduled_at: str,
        timezone_str: str = "Asia/Riyadh",
        privacy_status: str = "private",
    ) -> TupleResult:
        """Create a publishing job for a clip, preventing duplicate jobs for the same clip.

        Returns (job_dict, created_new).
        """
        now = _utc_now_iso()
        with self.transaction() as conn:
            existing = conn.execute(
                "SELECT * FROM publishing_jobs WHERE clip_id = ?", (clip_id,)
            ).fetchone()
            if existing:
                return dict(existing), False

            cur = conn.execute(
                """
                INSERT INTO publishing_jobs (
                    clip_id, scheduled_at, timezone, privacy_status,
                    status, attempt_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'scheduled', 0, ?, ?)
                """,
                (clip_id, scheduled_at, timezone_str, privacy_status, now, now),
            )
            conn.execute("UPDATE clips SET status = 'scheduled' WHERE id = ?", (clip_id,))
            row = conn.execute("SELECT * FROM publishing_jobs WHERE id = ?", (cur.lastrowid,)).fetchone()
            return dict(row), True

    def get_publishing_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM publishing_jobs WHERE id = ?", (job_id,)).fetchone()
            return dict(row) if row else None

    def get_publishing_job_by_clip(self, clip_id: int) -> Optional[Dict[str, Any]]:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM publishing_jobs WHERE clip_id = ?", (clip_id,)).fetchone()
            return dict(row) if row else None

    def list_publishing_jobs(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.transaction() as conn:
            query = """
                SELECT pj.*, c.sequence_number, c.total_parts, c.output_filename,
                       c.title, c.description, c.hashtags, c.content_hash, c.source_video_id,
                       sv.filename as source_filename, sv.rights_status
                FROM publishing_jobs pj
                JOIN clips c ON pj.clip_id = c.id
                JOIN source_videos sv ON c.source_video_id = sv.id
            """
            params: List[Any] = []
            if status:
                query += " WHERE pj.status = ?"
                params.append(status)
            query += " ORDER BY pj.scheduled_at ASC, c.sequence_number ASC"
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def update_publishing_job(
        self,
        job_id: int,
        status: Optional[str] = None,
        scheduled_at: Optional[str] = None,
        attempt_count: Optional[int] = None,
        upload_session_uri: Optional[str] = None,
        youtube_video_id: Optional[str] = None,
        youtube_url: Optional[str] = None,
        last_error: Optional[str] = None,
    ) -> None:
        fields = ["updated_at = ?"]
        params: List[Any] = [_utc_now_iso()]
        if status is not None:
            fields.append("status = ?")
            params.append(status)
        if scheduled_at is not None:
            fields.append("scheduled_at = ?")
            params.append(scheduled_at)
        if attempt_count is not None:
            fields.append("attempt_count = ?")
            params.append(attempt_count)
        if upload_session_uri is not None:
            fields.append("upload_session_uri = ?")
            params.append(upload_session_uri)
        if youtube_video_id is not None:
            fields.append("youtube_video_id = ?")
            params.append(youtube_video_id)
        if youtube_url is not None:
            fields.append("youtube_url = ?")
            params.append(youtube_url)
        if last_error is not None:
            fields.append("last_error = ?")
            params.append(scrub_secrets(last_error))

        params.append(job_id)
        with self.transaction() as conn:
            conn.execute(f"UPDATE publishing_jobs SET {', '.join(fields)} WHERE id = ?", params)
            if status == "published":
                job = conn.execute("SELECT clip_id FROM publishing_jobs WHERE id = ?", (job_id,)).fetchone()
                if job:
                    conn.execute("UPDATE clips SET status = 'published' WHERE id = ?", (job["clip_id"],))

    def recover_interrupted_operations(self) -> Dict[str, int]:
        """Reconcile interrupted states after a crash or connection drop.

        - Source videos stuck in 'processing' are reset to 'discovered' so they can resume cleanly.
        - Publishing jobs stuck in 'uploading' are marked 'reconcile_needed' (not blindly re-uploaded).
        """
        with self.transaction() as conn:
            cur_v = conn.execute(
                "UPDATE source_videos SET status = 'discovered' WHERE status = 'processing'"
            )
            cur_j = conn.execute(
                "UPDATE publishing_jobs SET status = 'reconcile_needed', updated_at = ? WHERE status = 'uploading'",
                (_utc_now_iso(),),
            )
            return {
                "recovered_videos": cur_v.rowcount,
                "reconcile_jobs": cur_j.rowcount,
            }

    # ------------------------------------------------------------------
    # Templates & Settings
    # ------------------------------------------------------------------
    def get_template(self, name: str = "default") -> Dict[str, Any]:
        with self.transaction() as conn:
            row = conn.execute("SELECT * FROM templates WHERE name = ?", (name,)).fetchone()
            if not row:
                return dict(DEFAULT_TEMPLATE_CONFIG)
            data = json.loads(row["configuration_json"])
            merged = dict(DEFAULT_TEMPLATE_CONFIG)
            merged.update(data)
            return merged

    def save_template(self, name: str, config: Dict[str, Any]) -> None:
        now = _utc_now_iso()
        merged = dict(DEFAULT_TEMPLATE_CONFIG)
        merged.update(config)
        config_str = json.dumps(merged, ensure_ascii=False)
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO templates (name, configuration_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    configuration_json = excluded.configuration_json,
                    updated_at = excluded.updated_at
                """,
                (name, config_str, now, now),
            )

    def get_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        with self.transaction() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        now = _utc_now_iso()
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (key, str(value), now),
            )

    def get_all_settings(self) -> Dict[str, str]:
        with self.transaction() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
            return {r["key"]: r["value"] for r in rows}

    # ------------------------------------------------------------------
    # Dashboard Summary Metrics
    # ------------------------------------------------------------------
    def get_dashboard_stats(self) -> Dict[str, Any]:
        with self.transaction() as conn:
            discovered = conn.execute("SELECT COUNT(*) as c FROM source_videos").fetchone()["c"]
            processing = conn.execute(
                "SELECT COUNT(*) as c FROM source_videos WHERE status = 'processing'"
            ).fetchone()["c"]
            ready_clips = conn.execute(
                "SELECT COUNT(*) as c FROM clips WHERE status = 'ready'"
            ).fetchone()["c"]
            scheduled_clips = conn.execute(
                "SELECT COUNT(*) as c FROM publishing_jobs WHERE status = 'scheduled'"
            ).fetchone()["c"]
            published_clips = conn.execute(
                "SELECT COUNT(*) as c FROM publishing_jobs WHERE status = 'published'"
            ).fetchone()["c"]
            failed_tasks = (
                conn.execute("SELECT COUNT(*) as c FROM source_videos WHERE status = 'failed'").fetchone()["c"]
                + conn.execute("SELECT COUNT(*) as c FROM publishing_jobs WHERE status = 'failed'").fetchone()["c"]
            )
            last_success_row = conn.execute(
                "SELECT created_at, operation_type, message FROM operation_logs WHERE status = 'success' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            next_pub_row = conn.execute(
                "SELECT scheduled_at FROM publishing_jobs WHERE status = 'scheduled' ORDER BY scheduled_at ASC LIMIT 1"
            ).fetchone()

            return {
                "discovered_videos": discovered,
                "processing_videos": processing,
                "ready_clips": ready_clips,
                "scheduled_clips": scheduled_clips,
                "published_clips": published_clips,
                "failed_tasks": failed_tasks,
                "last_successful_operation": dict(last_success_row) if last_success_row else None,
                "next_scheduled_publish": next_pub_row["scheduled_at"] if next_pub_row else None,
            }

    # ------------------------------------------------------------------
    # Backup & Restore
    # ------------------------------------------------------------------
    def backup_to(self, backup_path: Path | str) -> Path:
        backup_path = Path(backup_path)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        src_conn = self._connect()
        dst_conn = sqlite3.connect(str(backup_path))
        try:
            src_conn.backup(dst_conn)
        finally:
            dst_conn.close()
            src_conn.close()
        return backup_path

    def restore_from(self, backup_path: Path | str) -> None:
        backup_path = Path(backup_path)
        if not backup_path.exists():
            raise FileNotFoundError(f"Backup file not found: {backup_path}")
        shutil.copy2(backup_path, self.db_path)


TupleResult = Any

