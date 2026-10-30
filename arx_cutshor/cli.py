"""Command-Line Interface (CLI) for ARX-cutShor.

Provides commands for:
- `audit`: Check system environment, FFmpeg, database, and configuration.
- `discover`: Poll Google Drive for new source videos.
- `process`: Process pending videos or reprocess a specific video ID (`--reprocess-id`).
- `schedule`: Schedule ready clips into publishing slots.
- `publish`: Upload due scheduled clips (respects DRY_RUN and testing safety gates).
- `run-cycle`: Execute a full automated pipeline cycle and save a report.
- `report`: Output current dashboard status report as JSON.
- `maintenance`: Recover interrupted jobs, clean temporary files, and create SQLite backup.
- `serve`: Launch the local RTL Arabic Web Management Interface.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

from arx_cutshor.config import load_config
from arx_cutshor.database import Database
from arx_cutshor.pipeline import PipelineOrchestrator
from arx_cutshor.security import cleanup_temp_files


def cmd_audit(args) -> int:
    cfg = load_config()
    db = Database(cfg.database_path)
    orch = PipelineOrchestrator(cfg, db=db)
    report = {
        "project": "ARX-cutShor",
        "developer": "Abdullah Alasaad",
        "dry_run": cfg.dry_run,
        "publishing_mode": cfg.publishing_mode,
        "allow_public_publishing": cfg.allow_public_publishing,
        "ffmpeg_path": orch.video_engine.ffmpeg_path,
        "ffmpeg_available": shutil.which(orch.video_engine.ffmpeg_path) is not None or Path(orch.video_engine.ffmpeg_path).exists(),
        "database_path": str(cfg.database_path),
        "gdrive_enabled": cfg.gdrive_enabled,
        "youtube_enabled": cfg.youtube_enabled,
        "dashboard": db.get_dashboard_stats(),
    }
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def cmd_discover(args) -> int:
    cfg = load_config()
    orch = PipelineOrchestrator(cfg)
    folder_id = args.folder_id or cfg.gdrive_input_folder_id
    if not folder_id:
        print("Error: No Google Drive input folder ID configured.", file=sys.stderr)
        return 1
    discovered = orch.gdrive.discover_new_videos(folder_id=folder_id)
    print(json.dumps({"discovered_count": len(discovered), "videos": discovered}, indent=2, ensure_ascii=False))
    return 0


def cmd_process(args) -> int:
    cfg = load_config()
    if args.dry_run:
        cfg.dry_run = True
    orch = PipelineOrchestrator(cfg)

    if args.reprocess_id is not None:
        res = orch.process_source_video(int(args.reprocess_id), force_reprocess=True)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0

    if args.local_file:
        local_p = Path(args.local_file).resolve()
        if not local_p.exists():
            print(f"Error: File not found: {local_p}", file=sys.stderr)
            return 1
        sv, _ = orch.db.register_source_video(
            drive_file_id=f"local_{local_p.stem}_{local_p.stat().st_size}",
            filename=local_p.name,
            mime_type="video/mp4",
            size_bytes=local_p.stat().st_size,
            local_path=str(local_p),
        )
        res = orch.process_source_video(sv["id"], force_reprocess=args.force)
        print(json.dumps(res, indent=2, ensure_ascii=False))
        return 0

    pending = orch.db.list_source_videos(status="discovered")[: args.max_videos]
    results = []
    for sv in pending:
        res = orch.process_source_video(sv["id"])
        results.append(res)
    print(json.dumps({"processed": len(results), "results": results}, indent=2, ensure_ascii=False))
    return 0


def cmd_schedule(args) -> int:
    cfg = load_config()
    orch = PipelineOrchestrator(cfg)
    jobs = orch.scheduler.schedule_ready_clips(requested_privacy=cfg.get_effective_privacy(args.privacy))
    print(json.dumps({"scheduled_count": len(jobs), "jobs": jobs}, indent=2, ensure_ascii=False))
    return 0


def cmd_run_cycle(args) -> int:
    cfg = load_config()
    if args.dry_run:
        cfg.dry_run = True
    orch = PipelineOrchestrator(cfg)
    report = orch.run_cycle(max_videos=args.max_videos)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


def cmd_report(args) -> int:
    cfg = load_config()
    db = Database(cfg.database_path)
    stats = db.get_dashboard_stats()
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    return 0


def cmd_maintenance(args) -> int:
    cfg = load_config()
    db = Database(cfg.database_path)
    recovery = db.recover_interrupted_operations()
    cleaned = cleanup_temp_files(cfg.workspace_dir / "temp", max_age_seconds=args.max_age, max_total_mb=cfg.max_temp_storage_mb)
    backup_path = cfg.database_path.parent / "arx_cutshor_backup.db"
    db.backup_to(backup_path)
    print(
        json.dumps(
            {
                "recovery": recovery,
                "temp_files_cleaned": cleaned,
                "backup_created": str(backup_path),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def cmd_serve(args) -> int:
    import threading
    import time
    import uvicorn
    from arx_cutshor.web_app import create_app

    cfg = load_config()
    host = args.host or cfg.web_host
    port = int(args.port or cfg.web_port)
    app = create_app(cfg)

    if not getattr(args, "no_watch", False):
        def _auto_watcher():
            while True:
                try:
                    orch = PipelineOrchestrator(load_config())
                    orch.run_cycle(max_videos=2)
                except Exception as exc:
                    print(f"[Auto-Watcher] Warning: {exc}", file=sys.stderr)
                time.sleep(45)

        t = threading.Thread(target=_auto_watcher, daemon=True)
        t.start()
        print("🚀 [Auto-Watcher] المراقب التلقائي يعمل في الخلفية (يفحص Google Drive ويرفع تلقائياً كل 45 ثانية)")

    uvicorn.run(app, host=host, port=port)
    return 0


def cmd_watch(args) -> int:
    import time

    interval = max(15, int(args.interval))
    print(f"🚀 بدء المراقبة التلقائية المستمرة لمجلد Google Drive كل {interval} ثانية...")
    while True:
        try:
            cfg = load_config()
            orch = PipelineOrchestrator(cfg)
            report = orch.run_cycle(max_videos=args.max_videos)
            if report.get("newly_discovered") or report.get("processed_videos") or report.get("executed_publishing_jobs"):
                print(json.dumps(report, indent=2, ensure_ascii=False))
        except KeyboardInterrupt:
            print("\nتم إيقاف المراقب التلقائي.")
            break
        except Exception as exc:
            print(f"[Auto-Watcher Error] {exc}", file=sys.stderr)
        time.sleep(interval)
    return 0


def cmd_auth(args) -> int:
    cfg = load_config()
    orch = PipelineOrchestrator(cfg)
    target = args.service or "all"

    if target in ("all", "drive"):
        print("\n[Google Drive] بدء المصادقة... سيتم فتح المتصفح لمنح الإذن...")
        try:
            orch.gdrive.authenticate(interactive=True)
            print("✅ [Google Drive] تمت المصادقة بنجاح وتم حفظ الرمز في credentials/gdrive_token.json")
        except Exception as exc:
            print(f"❌ [Google Drive] فشلت المصادقة: {exc}", file=sys.stderr)
            return 1

    if target in ("all", "youtube"):
        print("\n[YouTube] بدء المصادقة... سيتم فتح المتصفح لمنح الإذن...")
        try:
            orch.youtube.authenticate(interactive=True)
            print("✅ [YouTube] تمت المصادقة بنجاح وتم حفظ الرمز في credentials/youtube_token.json")
        except Exception as exc:
            print(f"❌ [YouTube] فشلت المصادقة: {exc}", file=sys.stderr)
            return 1

    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="arx-cutshor", description="ARX-cutShor Automation CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("audit", help="Validate environment, FFmpeg, database, and config")

    p_auth = sub.add_parser("auth", help="Interactive OAuth 2.0 authentication for Google Drive and YouTube")
    p_auth.add_argument("--service", default="all", choices=["all", "drive", "youtube"], help="Service to authenticate")

    p_disc = sub.add_parser("discover", help="Discover new videos from Google Drive")
    p_disc.add_argument("--folder-id", default="", help="Override Google Drive Input Folder ID")

    p_proc = sub.add_parser("process", help="Process discovered videos or a specific video")
    p_proc.add_argument("--reprocess-id", type=int, default=None, help="Reprocess a specific source video ID")
    p_proc.add_argument("--local-file", default="", help="Register and process a local video file")
    p_proc.add_argument("--force", action="store_true", help="Force reprocessing even if already ready")
    p_proc.add_argument("--max-videos", type=int, default=2, help="Max pending videos to process")
    p_proc.add_argument("--dry-run", action="store_true", help="Force DRY_RUN mode")

    p_sched = sub.add_parser("schedule", help="Schedule ready clips")
    p_sched.add_argument("--privacy", default="private", choices=["private", "unlisted", "public"])

    p_cycle = sub.add_parser("run-cycle", help="Run full pipeline cycle")
    p_cycle.add_argument("--max-videos", type=int, default=2)
    p_cycle.add_argument("--dry-run", action="store_true")

    sub.add_parser("report", help="Print JSON status report")

    p_maint = sub.add_parser("maintenance", help="Recover interrupted jobs, clean temp files, and backup DB")
    p_maint.add_argument("--max-age", type=int, default=3600, help="Max temp file age in seconds")

    p_serve = sub.add_parser("serve", help="Start local RTL Arabic Management Web UI + Auto-Watcher")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8000)
    p_serve.add_argument("--no-watch", action="store_true", help="Disable background Google Drive watcher")

    p_watch = sub.add_parser("watch", help="Continuously watch Google Drive, process, and upload to YouTube")
    p_watch.add_argument("--interval", type=int, default=45, help="Poll interval in seconds")
    p_watch.add_argument("--max-videos", type=int, default=2, help="Max videos per cycle")

    args = parser.parse_args(argv)
    dispatch = {
        "audit": cmd_audit,
        "auth": cmd_auth,
        "discover": cmd_discover,
        "process": cmd_process,
        "schedule": cmd_schedule,
        "run-cycle": cmd_run_cycle,
        "report": cmd_report,
        "maintenance": cmd_maintenance,
        "serve": cmd_serve,
        "watch": cmd_watch,
    }
    return dispatch[args.command](args)


if __name__ == "__main__":
    sys.exit(main())

