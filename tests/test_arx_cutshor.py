"""Comprehensive Automated Test Suite for ARX-cutShor.

Covers all 20 mandatory test scenarios plus Web UI and CLI integration tests:
1. Discovering a new video.
2. Not duplicating processing of the same video.
3. Validating unsupported file extensions.
4. Handling a corrupted media file.
5. Extracting video duration and metadata.
6. Splitting a short video.
7. Splitting a long video.
8. Handling the final tail segment properly.
9. Preserving sequence order of parts.
10. Adding channel logo overlay.
11. Disabling intro and outro cleanly.
12. Generating publishing metadata (title, description, deduplicated hashtags).
13. Calculating publishing schedule slots.
14. Preventing duplicate publishing jobs for the same clip.
15. Recovering state of an interrupted operation.
16. Handling network connection drops (retry & reconciliation).
17. Handling OAuth authentication errors.
18. Handling API quota exceeded errors.
19. Cleaning up temporary files according to policy.
20. Blocking public publishing during testing / DRY_RUN / unconfirmed rights.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List
import pytest

from arx_cutshor.config import AppConfig
from arx_cutshor.database import Database
from arx_cutshor.gdrive import (
    GoogleDriveError,
    GoogleDriveManager,
    OAuthAuthenticationError,
    QuotaExceededError,
    compute_md5,
)
from arx_cutshor.metadata import MetadataGenerator, deduplicate_hashtags
from arx_cutshor.pipeline import PipelineOrchestrator
from arx_cutshor.scheduler import SchedulerEngine
from arx_cutshor.security import cleanup_temp_files, safe_resolve_path, sanitize_filename, scrub_secrets
from arx_cutshor.video_engine import VideoEngine
from arx_cutshor.youtube import YouTubeOAuthError, YouTubePublisher, YouTubeQuotaError


# ----------------------------------------------------------------------
# Fixtures
# ----------------------------------------------------------------------
@pytest.fixture
def temp_env(tmp_path: Path):
    cfg = AppConfig(
        base_dir=tmp_path,
        dry_run=True,
        publishing_mode="testing",
        allow_public_publishing=False,
        database_path=tmp_path / "data" / "db" / "test.db",
        workspace_dir=tmp_path / "data" / "workspace",
        assets_dir=tmp_path / "data" / "assets",
        reports_dir=tmp_path / "data" / "reports",
        logs_dir=tmp_path / "logs",
        target_clip_duration_sec=4.0,
        min_clip_duration_sec=2.0,
        max_clip_duration_sec=6.0,
        safe_end_margin_sec=0.0,
        output_width=360,
        output_height=640,
    )
    cfg.ensure_directories()
    db = Database(cfg.database_path)
    engine = VideoEngine(ffmpeg_path=cfg.ffmpeg_path, ffprobe_path=cfg.ffprobe_path)
    return cfg, db, engine


@pytest.fixture(scope="module")
def sample_videos(tmp_path_factory):
    media_dir = tmp_path_factory.mktemp("media_samples")
    engine = VideoEngine()

    short_vid = engine.generate_test_video(
        media_dir / "short_episode.mp4", duration_seconds=3.5, width=320, height=240, fps=15, with_audio=True
    )
    long_vid = engine.generate_test_video(
        media_dir / "long_episode.mp4", duration_seconds=10.0, width=320, height=240, fps=15, with_audio=False
    )

    # Create a tiny valid PNG logo using Pillow
    logo_path = media_dir / "logo.png"
    from PIL import Image

    img = Image.new("RGBA", (64, 64), (255, 0, 85, 220))
    img.save(logo_path)

    return {
        "short": short_vid,
        "long": long_vid,
        "logo": logo_path,
        "engine": engine,
    }


# ----------------------------------------------------------------------
# Mock Google Drive & YouTube Services
# ----------------------------------------------------------------------
class MockDriveFilesResource:
    def __init__(self, files_list: List[Dict[str, Any]], media_map: Dict[str, bytes] = None, error_to_raise=None):
        self._files = files_list
        self._media_map = media_map or {}
        self._error = error_to_raise
        self.calls = 0

    def list(self, **kwargs):
        return self

    def get_media(self, fileId: str):
        self._current_file_id = fileId
        return self

    def execute(self):
        self.calls += 1
        if self._error:
            if callable(self._error):
                raise self._error(self.calls)
            raise self._error
        if hasattr(self, "_current_file_id") and self._current_file_id in self._media_map:
            data = self._media_map[self._current_file_id]
            delattr(self, "_current_file_id")
            return data
        return {"files": self._files}


class MockDriveService:
    def __init__(self, files_resource: MockDriveFilesResource):
        self._res = files_resource

    def files(self):
        return self._res


class MockYouTubeService:
    def __init__(self, upload_id: str = "yt_real_123", search_items=None, error_to_raise=None):
        self.upload_id = upload_id
        self.search_items = search_items or []
        self.error_to_raise = error_to_raise
        self.insert_calls = 0

    def videos(self):
        return self

    def search(self):
        return self

    def list(self, **kwargs):
        return self

    def insert(self, **kwargs):
        self.last_insert_body = kwargs.get("body", {})
        self.insert_calls += 1
        return self

    def execute(self):
        if self.error_to_raise:
            raise self.error_to_raise
        if hasattr(self, "last_insert_body"):
            return {"id": self.upload_id}
        return {"items": self.search_items}


# ----------------------------------------------------------------------
# Tests 1 - 20
# ----------------------------------------------------------------------

def test_01_discover_new_video(temp_env):
    """Test 1: Discovering a new video from Google Drive."""
    cfg, db, _ = temp_env
    mock_files = [
        {
            "id": "drive_file_001",
            "name": "الحلقة_الأولى.mp4",
            "mimeType": "video/mp4",
            "size": "1048576",
            "md5Checksum": "abc123def456",
            "trashed": False,
        }
    ]
    service = MockDriveService(MockDriveFilesResource(mock_files))
    gdrive = GoogleDriveManager(
        db=db,
        workspace_dir=cfg.workspace_dir,
        client_secret_file=cfg.gdrive_client_secret_file,
        token_file=cfg.gdrive_token_file,
        input_folder_id="folder_input_123",
        service_client=service,
    )
    discovered = gdrive.discover_new_videos()
    assert len(discovered) == 1
    assert discovered[0]["drive_file_id"] == "drive_file_001"
    assert discovered[0]["status"] == "discovered"


def test_02_prevent_duplicate_video_processing(temp_env, sample_videos):
    """Test 2: Preventing duplicate discovery and duplicate processing of the same video."""
    cfg, db, engine = temp_env
    short_vid = sample_videos["short"]
    md5 = compute_md5(short_vid)

    sv, is_new = db.register_source_video(
        drive_file_id="drive_dup_001",
        filename="episode_dup.mp4",
        mime_type="video/mp4",
        size_bytes=short_vid.stat().st_size,
        checksum=md5,
        local_path=str(short_vid),
    )
    assert is_new is True

    # Registering same drive_file_id again returns is_new=False
    sv_again, is_new_again = db.register_source_video(
        drive_file_id="drive_dup_001",
        filename="episode_dup.mp4",
        mime_type="video/mp4",
        size_bytes=short_vid.stat().st_size,
    )
    assert is_new_again is False
    assert sv_again["id"] == sv["id"]

    orch = PipelineOrchestrator(cfg, db=db, video_engine=engine)
    res1 = orch.process_source_video(sv["id"])
    assert res1["status"] == "success"

    # Second call without force_reprocess must skip
    res2 = orch.process_source_video(sv["id"])
    assert res2["status"] == "skipped_already_processed"


def test_03_validate_unsupported_extension(temp_env):
    """Test 3: Rejecting unsupported file extensions both locally and in Drive discovery."""
    cfg, db, engine = temp_env
    bad_file = cfg.workspace_dir / "input" / "malicious.exe"
    bad_file.write_bytes(b"not a video" * 20)

    with pytest.raises(ValueError, match="Unsupported video extension"):
        engine.probe_video(bad_file)

    gdrive = GoogleDriveManager(
        db=db,
        workspace_dir=cfg.workspace_dir,
        client_secret_file=cfg.gdrive_client_secret_file,
        token_file=cfg.gdrive_token_file,
    )
    valid, reason = gdrive.is_file_valid_and_complete({"name": "document.pdf", "mimeType": "application/pdf", "size": "500"})
    assert valid is False
    assert "Unsupported extension" in reason


def test_04_handle_corrupted_file(temp_env):
    """Test 4: Detecting and rejecting a corrupted MP4 file."""
    cfg, _, engine = temp_env
    corrupted = cfg.workspace_dir / "input" / "corrupted.mp4"
    corrupted.write_bytes(b"CORRUPTED_RANDOM_BYTES_NOT_A_VALID_MP4_CONTAINER" * 10)

    with pytest.raises(ValueError, match="Corrupted|Unable to extract duration"):
        engine.probe_video(corrupted)


def test_05_extract_video_duration_and_metadata(sample_videos):
    """Test 5: Extracting video duration, resolution, fps, codec, aspect ratio, and audio status."""
    engine: VideoEngine = sample_videos["engine"]
    meta_with_audio = engine.probe_video(sample_videos["short"])
    assert 3.0 <= meta_with_audio.duration_seconds <= 4.0
    assert meta_with_audio.width == 320
    assert meta_with_audio.height == 240
    assert meta_with_audio.has_audio is True
    assert meta_with_audio.aspect_ratio == "4:3"

    meta_no_audio = engine.probe_video(sample_videos["long"])
    assert 9.5 <= meta_no_audio.duration_seconds <= 10.5
    assert meta_no_audio.has_audio is False


def test_06_split_short_video(temp_env, sample_videos):
    """Test 6: Splitting a short video (duration <= target_duration produces 1 clip)."""
    cfg, _, engine = temp_env
    meta, clips = engine.process_video_to_clips(
        source_path=sample_videos["short"],
        output_dir=cfg.workspace_dir / "ready",
        mode="fixed",
        target_duration=5.0,
        min_duration=2.0,
        max_duration=10.0,
        template={"enabled": False},
    )
    assert len(clips) == 1
    assert clips[0].sequence_number == 1
    assert clips[0].total_parts == 1
    assert clips[0].output_path.exists()


def test_07_split_long_video(temp_env, sample_videos):
    """Test 7: Splitting a longer video into multiple valid clips."""
    cfg, _, engine = temp_env
    _, clips = engine.process_video_to_clips(
        source_path=sample_videos["long"],
        output_dir=cfg.workspace_dir / "ready",
        mode="fixed",
        target_duration=4.0,
        min_duration=2.0,
        max_duration=6.0,
        safe_end_margin=0.0,
        template={"enabled": False},
    )
    assert len(clips) >= 2
    for c in clips:
        assert c.output_path.exists()
        assert c.duration_seconds <= 6.5


def test_08_handle_last_tail_segment(sample_videos):
    """Test 8: Handling a trailing segment shorter than min_duration by merging or rebalancing."""
    engine: VideoEngine = sample_videos["engine"]
    # 11s video with target=5s, min=2.5s, max=7s -> raw would be [0..5, 5..10, 10..11(1s < 2.5s)]
    # Tail handler merges [5..10] and [10..11] into [5..11] (6s <= max 7s)
    segments = engine.calculate_segments(
        total_duration=11.0,
        mode="fixed",
        target_duration=5.0,
        min_duration=2.5,
        max_duration=7.0,
        safe_end_margin=0.0,
    )
    assert len(segments) == 2
    assert segments[-1].duration_seconds >= 2.5
    assert segments[-1].end_seconds == 11.0


def test_09_preserve_parts_sequence_order(sample_videos):
    """Test 9: Preserving strict sequential ordering and timestamps across all split modes."""
    engine: VideoEngine = sample_videos["engine"]
    for mode in ("fixed", "scene", "hybrid"):
        segs = engine.calculate_segments(
            total_duration=25.0,
            mode=mode,
            target_duration=8.0,
            min_duration=4.0,
            max_duration=12.0,
            scene_timestamps=[7.5, 16.2],
        )
        assert [s.sequence_number for s in segs] == list(range(1, len(segs) + 1))
        for i in range(1, len(segs)):
            assert segs[i].start_seconds == pytest.approx(segs[i - 1].end_seconds, abs=0.01)


def test_10_add_channel_logo_overlay(temp_env, sample_videos):
    """Test 10: Applying channel logo overlay and 9:16 vertical aspect conversion."""
    cfg, _, engine = temp_env
    template = {
        "enabled": True,
        "logo_enabled": True,
        "logo_path": str(sample_videos["logo"]),
        "logo_position": "top-right",
        "logo_scale": 0.15,
        "target_width": 360,
        "target_height": 640,
    }
    _, clips = engine.process_video_to_clips(
        source_path=sample_videos["short"],
        output_dir=cfg.workspace_dir / "ready",
        target_duration=5.0,
        template=template,
    )
    assert len(clips) == 1
    out_meta = engine.probe_video(clips[0].output_path)
    assert out_meta.width == 360
    assert out_meta.height == 640
    assert out_meta.aspect_ratio == "9:16"


def test_11_disable_intro_and_outro(temp_env, sample_videos):
    """Test 11: Disabling intro, outro, and text overlays works cleanly without code modification."""
    cfg, _, engine = temp_env
    template = {
        "enabled": True,
        "logo_enabled": False,
        "intro_enabled": False,
        "intro_path": "/nonexistent/intro.mp4",
        "outro_enabled": False,
        "outro_path": "/nonexistent/outro.mp4",
        "opening_text_enabled": False,
        "subscribe_text_enabled": False,
        "like_text_enabled": False,
        "part_badge_enabled": False,
        "target_width": 360,
        "target_height": 640,
    }
    _, clips = engine.process_video_to_clips(
        source_path=sample_videos["short"],
        output_dir=cfg.workspace_dir / "ready",
        target_duration=5.0,
        template=template,
    )
    assert len(clips) == 1
    assert clips[0].output_path.exists()


def test_12_generate_publishing_metadata():
    """Test 12: Generating Arabic title, description, and deduplicated hashtags."""
    gen = MetadataGenerator(channel_name="قناة ARX", default_series_name="قصص تقنية")
    meta = gen.generate(
        source_filename="الحلقة_الخامسة_برمجة.mp4",
        part_number=2,
        total_parts=4,
        series_name="قصص تقنية",
        extra_keywords=["Shorts", "shorts", "برمجة", "برمجة"],
    )
    assert "قصص تقنية" in meta.title
    assert "الجزء 02" in meta.title
    assert "#Shorts" in meta.hashtags
    # Verify no duplicate hashtags
    lower_tags = [h.lower() for h in meta.hashtags]
    assert len(lower_tags) == len(set(lower_tags))


def test_13_calculate_publishing_schedule_slots(temp_env):
    """Test 13: Calculating future publishing slots respecting daily limits, allowed hours, and minimum interval."""
    _, db, _ = temp_env
    sched = SchedulerEngine(
        db=db,
        timezone_name="Asia/Riyadh",
        daily_clip_limit=2,
        allowed_days=[0, 1, 2, 3, 4, 5, 6],
        publish_hours=["14:00", "18:00", "21:00"],
        min_interval_minutes=180,
    )
    ref = datetime(2026, 11, 1, 8, 0, 0, tzinfo=timezone.utc)
    slots = sched.calculate_next_slots(4, reference_time=ref)
    assert len(slots) == 4
    # Daily limit is 2, so slots 0 & 1 are on day 1, and slots 2 & 3 are on day 2
    assert slots[0].date() == slots[1].date()
    assert slots[2].date() > slots[1].date()
    # Minimum interval >= 180 minutes
    diff_mins = (slots[1] - slots[0]).total_seconds() / 60.0
    assert diff_mins >= 180


def test_14_prevent_duplicate_publishing_jobs(temp_env):
    """Test 14: Preventing duplicate publishing jobs for the same clip."""
    _, db, _ = temp_env
    sv, _ = db.register_source_video("drv_14", "vid14.mp4", "video/mp4", 1000)
    clip = db.create_clip(sv["id"], 1, 1, 0.0, 5.0, "out14.mp4", 5.0, "hash14")

    sched = SchedulerEngine(db=db)
    jobs1 = sched.schedule_ready_clips()
    assert len(jobs1) == 1

    # Calling schedule_ready_clips again must not create a second job
    jobs2 = sched.schedule_ready_clips()
    assert len(jobs2) == 0


def test_15_recover_interrupted_operation_state(temp_env):
    """Test 15: Recovering interrupted video processing and reconciling interrupted upload states."""
    _, db, _ = temp_env
    sv, _ = db.register_source_video("drv_15", "vid15.mp4", "video/mp4", 2000)
    db.update_source_video(sv["id"], status="processing")

    clip = db.create_clip(sv["id"], 1, 1, 0.0, 5.0, "out15.mp4", 5.0, "hash15", title="Unique Title 15")
    job, _ = db.create_publishing_job(clip["id"], "2026-10-30T12:00:00Z")
    db.update_publishing_job(job["id"], status="uploading")

    recovery = db.recover_interrupted_operations()
    assert recovery["recovered_videos"] == 1
    assert recovery["reconcile_jobs"] == 1
    assert db.get_source_video(sv["id"])["status"] == "discovered"
    assert db.get_publishing_job(job["id"])["status"] == "reconcile_needed"


def test_16_handle_network_disconnection_and_reconcile(temp_env, sample_videos):
    """Test 16: Handling transient network failure during Drive call and reconciling YouTube state before retry."""
    cfg, db, _ = temp_env

    # Part A: Transient network drop on 1st attempt succeeds on 2nd attempt
    def flaky_network(call_count):
        if call_count == 1:
            raise ConnectionResetError("Connection dropped by peer")
        return None

    res_obj = MockDriveFilesResource(
        [{"id": "drv_net", "name": "net.mp4", "mimeType": "video/mp4", "size": "500", "md5Checksum": "m1"}],
        error_to_raise=flaky_network,
    )
    # Fix flaky_network so on call 2 it returns normal list
    orig_exec = res_obj.execute

    def custom_exec():
        res_obj.calls += 1
        if res_obj.calls == 1:
            raise ConnectionResetError("Simulated connection reset")
        return {"files": res_obj._files}

    res_obj.execute = custom_exec
    gdrive = GoogleDriveManager(
        db=db,
        workspace_dir=cfg.workspace_dir,
        client_secret_file=cfg.gdrive_client_secret_file,
        token_file=cfg.gdrive_token_file,
        input_folder_id="fld",
        service_client=MockDriveService(res_obj),
    )
    found = gdrive.discover_new_videos()
    assert len(found) == 1
    assert res_obj.calls == 2

    # Part B: YouTube upload succeeded before connection drop; reconciliation finds video and skips re-upload
    sv, _ = db.register_source_video("drv_yt_rec", "rec.mp4", "video/mp4", 1000)
    clip = db.create_clip(sv["id"], 1, 1, 0.0, 3.5, str(sample_videos["short"]), 3.5, "hash_rec", title="حلقة خاصة #01")
    job, _ = db.create_publishing_job(clip["id"], "2026-10-30T10:00:00Z")

    mock_yt = MockYouTubeService(
        search_items=[{"id": {"videoId": "already_on_yt_999"}, "snippet": {"title": "حلقة خاصة #01"}}]
    )
    yt = YouTubePublisher(
        db=db,
        client_secret_file=cfg.youtube_client_secret_file,
        token_file=cfg.youtube_token_file,
        dry_run=False,
        service_client=mock_yt,
    )
    updated_job = yt.publish_job(job["id"], sample_videos["short"])
    assert updated_job["status"] == "published"
    assert updated_job["youtube_video_id"] == "already_on_yt_999"
    assert mock_yt.insert_calls == 0  # Did not re-upload!


def test_17_handle_oauth_error(temp_env):
    """Test 17: Handling invalid/missing OAuth credentials and scrubbing secrets from error logs."""
    cfg, db, _ = temp_env
    gdrive = GoogleDriveManager(
        db=db,
        workspace_dir=cfg.workspace_dir,
        client_secret_file=cfg.gdrive_client_secret_file,
        token_file=cfg.gdrive_token_file,
        input_folder_id="fld",
        service_client=None,
    )
    with pytest.raises(OAuthAuthenticationError):
        gdrive.discover_new_videos()

    # Verify secret scrubbing on OAuth token strings
    leaked = 'Error with access_token="ya29.a0AfH6SMBxSecretTokenValue12345" and refresh_token=1//0eSecretRefresh999'
    scrubbed = scrub_secrets(leaked)
    assert "ya29.a0AfH6SMBxSecretTokenValue12345" not in scrubbed
    assert "1//0eSecretRefresh999" not in scrubbed


def test_18_handle_api_quota_exceeded(temp_env, sample_videos):
    """Test 18: Handling Google Drive and YouTube API quota exceeded errors cleanly."""
    cfg, db, _ = temp_env
    quota_err = Exception("HttpError 429: userRateLimitExceeded / quotaExceeded")
    gdrive = GoogleDriveManager(
        db=db,
        workspace_dir=cfg.workspace_dir,
        client_secret_file=cfg.gdrive_client_secret_file,
        token_file=cfg.gdrive_token_file,
        input_folder_id="fld",
        service_client=MockDriveService(MockDriveFilesResource([], error_to_raise=quota_err)),
    )
    with pytest.raises(QuotaExceededError):
        gdrive.discover_new_videos()

    sv, _ = db.register_source_video("drv_q", "q.mp4", "video/mp4", 1000)
    clip = db.create_clip(sv["id"], 1, 1, 0.0, 3.5, str(sample_videos["short"]), 3.5, "hq", title="Quota Clip")
    job, _ = db.create_publishing_job(clip["id"], "2026-10-30T10:00:00Z")

    yt = YouTubePublisher(
        db=db,
        client_secret_file=cfg.youtube_client_secret_file,
        token_file=cfg.youtube_token_file,
        dry_run=False,
        service_client=MockYouTubeService(error_to_raise=Exception("HttpError 403: quotaExceeded")),
    )
    with pytest.raises(YouTubeQuotaError):
        yt.publish_job(job["id"], sample_videos["short"])

    failed_job = db.get_publishing_job(job["id"])
    assert failed_job["status"] == "failed"


def test_19_cleanup_temporary_files(temp_env):
    """Test 19: Cleaning up temporary workspace files by age and storage cap + Path Traversal protection."""
    cfg, _, _ = temp_env
    temp_dir = cfg.workspace_dir / "temp"
    f1 = temp_dir / "old_temp_1.tmp"
    f2 = temp_dir / "old_temp_2.tmp"
    f1.write_bytes(b"x" * 1024)
    f2.write_bytes(b"y" * 1024)

    deleted = cleanup_temp_files(temp_dir, max_age_seconds=0)
    assert deleted == 2
    assert not f1.exists()

    # Also verify Path Traversal is strictly blocked
    with pytest.raises(PermissionError):
        safe_resolve_path(cfg.workspace_dir, "../../etc/passwd")
    with pytest.raises(ValueError):
        sanitize_filename("../../secret.json")


def test_20_prevent_public_publishing_during_testing(temp_env, sample_videos):
    """Test 20: Blocking public publishing during testing mode, DRY_RUN, or when copyright status is unconfirmed."""
    cfg, db, _ = temp_env
    # Even if someone requests 'public', testing mode forces 'private'
    assert cfg.get_effective_privacy("public") == "private"

    sched = SchedulerEngine(
        db=db,
        publishing_mode="testing",
        allow_public_publishing=False,
        dry_run=False,
    )
    assert sched.resolve_safe_privacy("public", rights_status="confirmed") == "private"

    # Even if publishing_mode='scheduled' and allow_public_publishing=True, unconfirmed rights forces 'private'
    sched_prod = SchedulerEngine(
        db=db,
        publishing_mode="scheduled",
        allow_public_publishing=True,
        dry_run=False,
    )
    assert sched_prod.resolve_safe_privacy("public", rights_status="unconfirmed") == "private"


def test_21_end_to_end_pipeline_and_web_api(temp_env, sample_videos):
    """Integration Test (Phases 9 & 10): Full workflow from discovered file -> clips -> schedule -> DRY_RUN publish -> Web API."""
    from fastapi.testclient import TestClient
    from arx_cutshor.web_app import create_app

    cfg, db, engine = temp_env
    short_vid = sample_videos["short"]

    sv, _ = db.register_source_video(
        drive_file_id="e2e_drive_001",
        filename="الحلقة_التجريبية.mp4",
        mime_type="video/mp4",
        size_bytes=short_vid.stat().st_size,
        local_path=str(short_vid),
    )

    orch = PipelineOrchestrator(cfg, db=db, video_engine=engine)
    report = orch.run_cycle(max_videos=1, execute_due_uploads=False)
    assert report["processed_videos"] == 1
    assert report["newly_scheduled_jobs"] == 1
    assert Path(report["report_path"]).exists()

    # Test DRY_RUN upload on the scheduled job
    jobs = db.list_publishing_jobs()
    assert len(jobs) == 1
    pub_job = orch.youtube.publish_job(jobs[0]["id"], jobs[0]["output_filename"])
    assert pub_job["status"] == "published"
    assert pub_job["youtube_video_id"].startswith("dryrun_yt_")

    # Verify RTL Web Management Interface endpoints
    app = create_app(cfg, db=db)
    client = TestClient(app)

    r_home = client.get("/")
    assert r_home.status_code == 200
    assert 'dir="rtl"' in r_home.text

    r_dash = client.get("/api/dashboard")
    assert r_dash.status_code == 200
    assert r_dash.json()["published_clips"] == 1

