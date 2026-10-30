"""Arabic RTL Management Interface & REST API for ARX-cutShor.

Provides all 7 required management views:
1. Dashboard (لوحة القيادة)
2. Sources (مصادر Google Drive)
3. Processing Queue (قائمة انتظار المعالجة)
4. Templates (قوالب الفيديو والهوية البصرية)
5. Publishing Schedule (جدول النشر)
6. Settings (الإعدادات)
7. Logs (سجلات العمليات والأخطاء دون إظهار الأسرار)

Protected locally via token / localhost binding.
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

from arx_cutshor.config import AppConfig, load_config
from arx_cutshor.database import Database
from arx_cutshor.pipeline import PipelineOrchestrator
from arx_cutshor.security import scrub_secrets

STATIC_DIR = Path(__file__).resolve().parent / "web" / "static"


def create_app(config: Optional[AppConfig] = None, db: Optional[Database] = None):
    """Create the FastAPI application for ARX-cutShor Management UI."""
    from fastapi import Depends, FastAPI, Header, HTTPException, Request
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    cfg = config or load_config()
    database = db or Database(cfg.database_path)
    orchestrator = PipelineOrchestrator(config=cfg, db=database)

    app = FastAPI(
        title="ARX-cutShor Management Platform",
        description="منصة أتمتة إنتاج ونشر مقاطع YouTube Shorts",
        version="1.0.0",
    )

    def verify_local_auth(
        request: Request,
        x_admin_token: Optional[str] = Header(default=None),
    ):
        """Ensure requests come from localhost or provide the configured admin token."""
        client_host = request.client.host if request.client else "127.0.0.1"
        if client_host in ("127.0.0.1", "::1", "localhost", "testclient"):
            return True
        if cfg.admin_access_token and x_admin_token == cfg.admin_access_token:
            return True
        raise HTTPException(status_code=403, detail="غير مصرح بالوصول عن بعد بدون رمز الحماية")

    @app.get("/", response_class=HTMLResponse)
    def index_page(auth: bool = Depends(verify_local_auth)):
        html_file = STATIC_DIR / "index.html"
        if html_file.exists():
            return HTMLResponse(html_file.read_text(encoding="utf-8"))
        return HTMLResponse("<html dir='rtl'><body><h1>ARX-cutShor</h1></body></html>")

    @app.get("/api/dashboard")
    def api_dashboard(auth: bool = Depends(verify_local_auth)):
        stats = database.get_dashboard_stats()
        stats["dry_run"] = cfg.dry_run
        stats["publishing_mode"] = database.get_setting("publishing_mode", cfg.publishing_mode)
        stats["scheduler_paused"] = orchestrator.scheduler.is_paused()
        return stats

    @app.get("/api/sources")
    def api_sources(auth: bool = Depends(verify_local_auth)):
        videos = database.list_source_videos()
        return {
            "gdrive_enabled": cfg.gdrive_enabled,
            "input_folder_id": cfg.gdrive_input_folder_id or "غير محدد (وضع محلي)",
            "last_scan": database.get_setting("last_gdrive_scan", "لم يتم الفحص بعد"),
            "total_discovered": len(videos),
            "files": videos,
        }

    @app.get("/api/queue")
    def api_processing_queue(auth: bool = Depends(verify_local_auth)):
        videos = database.list_source_videos()
        queue_items = []
        for v in videos:
            clips = database.list_clips(source_video_id=v["id"])
            progress = 100 if v["status"] in ("ready", "completed", "published") else (50 if v["status"] == "processing" else 0)
            queue_items.append(
                {
                    "id": v["id"],
                    "filename": v["filename"],
                    "duration_seconds": v["duration_seconds"],
                    "clips_count": len(clips),
                    "status": v["status"],
                    "rights_status": v["rights_status"],
                    "progress_percent": progress,
                    "error_message": scrub_secrets(v.get("error_message") or ""),
                }
            )
        return {"queue": queue_items}

    @app.post("/api/queue/{video_id}/retry")
    def api_retry_video(video_id: int, auth: bool = Depends(verify_local_auth)):
        sv = database.get_source_video(video_id)
        if not sv:
            raise HTTPException(status_code=404, detail="الفيديو غير موجود")
        database.reset_source_video_for_reprocessing(video_id)
        database.log_operation("queue_retry", "info", f"تمت جدولة إعادة معالجة الفيديو #{video_id}", str(video_id))
        return {"status": "reset", "video_id": video_id}

    @app.post("/api/queue/{video_id}/cancel")
    def api_cancel_video(video_id: int, auth: bool = Depends(verify_local_auth)):
        sv = database.get_source_video(video_id)
        if not sv:
            raise HTTPException(status_code=404, detail="الفيديو غير موجود")
        database.update_source_video(video_id, status="cancelled", error_message="تم الإلغاء من قبل المستخدم")
        database.log_operation("queue_cancel", "info", f"تم إلغاء معالجة الفيديو #{video_id}", str(video_id))
        return {"status": "cancelled", "video_id": video_id}

    @app.get("/api/templates")
    def api_get_templates(auth: bool = Depends(verify_local_auth)):
        return {"default": database.get_template("default")}

    @app.post("/api/templates")
    async def api_save_template(request: Request, auth: bool = Depends(verify_local_auth)):
        payload = await request.json()
        name = payload.get("name", "default")
        config_data = payload.get("configuration", payload)
        database.save_template(name, config_data)
        database.log_operation("template_update", "success", f"تم تحديث قالب الهوية البصرية '{name}'", name)
        return {"status": "saved", "template": database.get_template(name)}

    @app.get("/api/schedule")
    def api_get_schedule(auth: bool = Depends(verify_local_auth)):
        jobs = database.list_publishing_jobs()
        return {
            "paused": orchestrator.scheduler.is_paused(),
            "timezone": cfg.timezone,
            "daily_clip_limit": cfg.daily_clip_limit,
            "publish_hours": cfg.publish_hours,
            "min_interval_minutes": cfg.min_interval_minutes,
            "jobs": jobs,
        }

    @app.post("/api/schedule/pause")
    def api_pause_schedule(auth: bool = Depends(verify_local_auth)):
        orchestrator.scheduler.pause()
        return {"paused": True}

    @app.post("/api/schedule/resume")
    def api_resume_schedule(auth: bool = Depends(verify_local_auth)):
        orchestrator.scheduler.resume()
        return {"paused": False}

    @app.get("/api/settings")
    def api_get_settings(auth: bool = Depends(verify_local_auth)):
        settings = database.get_all_settings()
        return {
            "settings": settings,
            "runtime": {
                "dry_run": cfg.dry_run,
                "publishing_mode": cfg.publishing_mode,
                "allow_public_publishing": cfg.allow_public_publishing,
                "timezone": cfg.timezone,
                "split_mode": cfg.split_mode,
                "target_clip_duration_sec": cfg.target_clip_duration_sec,
                "min_clip_duration_sec": cfg.min_clip_duration_sec,
                "max_clip_duration_sec": cfg.max_clip_duration_sec,
                "max_temp_storage_mb": cfg.max_temp_storage_mb,
                "gdrive_enabled": cfg.gdrive_enabled,
                "youtube_enabled": cfg.youtube_enabled,
            },
        }

    @app.post("/api/settings")
    async def api_update_settings(request: Request, auth: bool = Depends(verify_local_auth)):
        payload = await request.json()
        for k, v in payload.items():
            # Never store raw secrets in the settings table
            if any(sec in k.lower() for sec in ("secret", "token", "password", "api_key")):
                continue
            database.set_setting(str(k), str(v))
        database.log_operation("settings_update", "success", "تم تحديث إعدادات النظام")
        return {"status": "updated", "settings": database.get_all_settings()}

    @app.get("/api/logs")
    def api_get_logs(limit: int = 100, auth: bool = Depends(verify_local_auth)):
        logs = database.get_logs(limit=min(limit, 500))
        for item in logs:
            item["message"] = scrub_secrets(item.get("message", ""))
        return {"logs": logs}

    return app
