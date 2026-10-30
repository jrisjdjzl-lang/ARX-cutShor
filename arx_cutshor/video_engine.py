"""Video Processing and Branding Engine for ARX-cutShor.

Supports:
- Validation of MP4, MOV, MKV, WebM files.
- Metadata extraction via ffprobe or ffmpeg stderr parser fallback.
- Handling videos without audio streams or with non-standard aspect ratios.
- Splitting modes: Fixed Duration, Scene-Based, Manual Markers, Hybrid.
- Channel Branding Template application (Logo, Intro, Outro, Arabic/RTL texts, Subscribe/Like overlays, 9:16 vertical conversion).
- Strictly compliant with copyright rules: no speed/pitch/color manipulation to evade Content ID.
"""

import hashlib
import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from arx_cutshor.security import sanitize_filename

SUPPORTED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}


@dataclass
class VideoMetadata:
    """Extracted metadata from a source video file."""

    filepath: Path
    duration_seconds: float
    width: int
    height: int
    fps: float
    video_codec: str
    has_audio: bool
    audio_codec: Optional[str]
    size_bytes: int
    aspect_ratio: str
    is_valid: bool = True
    error_message: Optional[str] = None


@dataclass
class ClipSegment:
    """Represents a calculated split segment before or after rendering."""

    sequence_number: int
    total_parts: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    output_path: Optional[Path] = None
    content_hash: str = ""


def compute_file_hash(filepath: Path, chunk_size: int = 65536) -> str:
    """Compute SHA-256 hash of a file."""
    sha = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(chunk_size):
            sha.update(chunk)
    return sha.hexdigest()


def _calculate_aspect_ratio(width: int, height: int) -> str:
    if width <= 0 or height <= 0:
        return "unknown"
    g = math.gcd(width, height)
    return f"{width // g}:{height // g}"


def shape_arabic_text(text: str) -> str:
    """Shape Arabic text for FFmpeg drawtext filter if arabic_reshaper & bidi are installed."""
    if not text:
        return ""
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display

        reshaped = arabic_reshaper.reshape(text)
        return get_display(reshaped)
    except Exception:
        return text


def _escape_ffmpeg_drawtext(text: str) -> str:
    """Escape special characters for FFmpeg drawtext filter."""
    escaped = text.replace("\\", "\\\\")
    escaped = escaped.replace(":", "\\:")
    escaped = escaped.replace("'", "\\'")
    escaped = escaped.replace("%", "\\%")
    return escaped


class VideoEngine:
    """FFmpeg/ffprobe powered video inspection, splitting, and branding engine."""

    def __init__(self, ffmpeg_path: str = "ffmpeg", ffprobe_path: str = "ffprobe"):
        self.ffmpeg_path = self._resolve_ffmpeg_binary(ffmpeg_path)
        self.ffprobe_path = self._resolve_ffprobe_binary(ffprobe_path)

    def _resolve_ffmpeg_binary(self, requested: str) -> str:
        found = shutil.which(requested)
        if found:
            return found
        try:
            import imageio_ffmpeg

            exe = imageio_ffmpeg.get_ffmpeg_exe()
            if exe and Path(exe).exists():
                return exe
        except Exception:
            pass
        return requested

    def _resolve_ffprobe_binary(self, requested: str) -> Optional[str]:
        found = shutil.which(requested)
        if found:
            return found
        return None

    # ------------------------------------------------------------------
    # Validation & Metadata Extraction
    # ------------------------------------------------------------------
    def probe_video(self, filepath: Path | str) -> VideoMetadata:
        """Validate video file and extract duration, resolution, fps, audio, size, codec, and aspect ratio."""
        path = Path(filepath).resolve()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Video file does not exist: {path}")

        ext = path.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            raise ValueError(f"Unsupported video extension '{ext}'. Supported: {sorted(SUPPORTED_EXTENSIONS)}")

        size_bytes = path.stat().st_size
        if size_bytes < 64:
            raise ValueError(f"Video file is empty or corrupted (size={size_bytes} bytes): {path.name}")

        if self.ffprobe_path:
            try:
                return self._probe_with_ffprobe(path, size_bytes)
            except Exception:
                pass

        return self._probe_with_ffmpeg_stderr(path, size_bytes)

    def _probe_with_ffprobe(self, path: Path, size_bytes: int) -> VideoMetadata:
        cmd = [
            self.ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=index,codec_type,codec_name,width,height,r_frame_rate",
            "-of",
            "json",
            str(path),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        if res.returncode != 0:
            raise ValueError(f"Corrupted or unreadable media file ({path.name}): {res.stderr.strip()}")

        data = json.loads(res.stdout or "{}")
        streams = data.get("streams", [])
        fmt = data.get("format", {})

        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        if not video_stream:
            raise ValueError(f"No valid video stream found in {path.name}")

        duration = float(fmt.get("duration") or video_stream.get("duration") or 0.0)
        if duration <= 0:
            raise ValueError(f"Invalid video duration ({duration}s) in {path.name}")

        width = int(video_stream.get("width") or 0)
        height = int(video_stream.get("height") or 0)
        fps_raw = video_stream.get("r_frame_rate", "30/1")
        try:
            if "/" in fps_raw:
                num, den = fps_raw.split("/", 1)
                fps = float(num) / float(den) if float(den) != 0 else 30.0
            else:
                fps = float(fps_raw)
        except Exception:
            fps = 30.0

        return VideoMetadata(
            filepath=path,
            duration_seconds=round(duration, 3),
            width=width,
            height=height,
            fps=round(fps, 2),
            video_codec=video_stream.get("codec_name", "unknown"),
            has_audio=audio_stream is not None,
            audio_codec=audio_stream.get("codec_name") if audio_stream else None,
            size_bytes=size_bytes,
            aspect_ratio=_calculate_aspect_ratio(width, height),
            is_valid=True,
        )

    def _probe_with_ffmpeg_stderr(self, path: Path, size_bytes: int) -> VideoMetadata:
        """Fallback probe using `ffmpeg -i <file>` stderr parsing when ffprobe is not separately installed."""
        cmd = [self.ffmpeg_path, "-hide_banner", "-i", str(path)]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
        output = res.stderr or ""

        if "Invalid data found when processing input" in output or "moov atom not found" in output:
            raise ValueError(f"Corrupted media file ({path.name}): invalid container or stream data")

        dur_match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", output)
        if not dur_match:
            raise ValueError(f"Unable to extract duration from {path.name}. File may be corrupted.")

        hours, mins, secs = int(dur_match.group(1)), int(dur_match.group(2)), float(dur_match.group(3))
        duration = hours * 3600 + mins * 60 + secs
        if duration <= 0:
            raise ValueError(f"Video duration is zero in {path.name}")

        video_match = re.search(
            r"Stream #\d+:\d+.*?: Video:\s*([a-zA-Z0-9_]+).*?,\s*(\d+)x(\d+)", output
        )
        if not video_match:
            raise ValueError(f"No valid video stream detected in {path.name}")

        video_codec = video_match.group(1)
        width = int(video_match.group(2))
        height = int(video_match.group(3))

        fps_match = re.search(r"(\d+(?:\.\d+)?)\s*fps", output)
        fps = float(fps_match.group(1)) if fps_match else 30.0

        audio_match = re.search(r"Stream #\d+:\d+.*?: Audio:\s*([a-zA-Z0-9_]+)", output)
        has_audio = audio_match is not None
        audio_codec = audio_match.group(1) if audio_match else None

        return VideoMetadata(
            filepath=path,
            duration_seconds=round(duration, 3),
            width=width,
            height=height,
            fps=round(fps, 2),
            video_codec=video_codec,
            has_audio=has_audio,
            audio_codec=audio_codec,
            size_bytes=size_bytes,
            aspect_ratio=_calculate_aspect_ratio(width, height),
            is_valid=True,
        )

    # ------------------------------------------------------------------
    # Scene Detection Helper (for Scene-Based and Hybrid modes)
    # ------------------------------------------------------------------
    def detect_scene_changes(self, filepath: Path, threshold: float = 0.35) -> List[float]:
        """Detect scene change timestamps using FFmpeg's select='gt(scene,threshold)' filter."""
        cmd = [
            self.ffmpeg_path,
            "-hide_banner",
            "-i",
            str(filepath),
            "-vf",
            f"select='gt(scene,{threshold})',showinfo",
            "-f",
            "null",
            "-",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        timestamps: List[float] = []
        for match in re.finditer(r"pts_time:\s*(\d+(?:\.\d+)?)", res.stderr or ""):
            ts = float(match.group(1))
            if ts > 0:
                timestamps.append(round(ts, 3))
        return sorted(set(timestamps))

    # ------------------------------------------------------------------
    # Splitting Calculation Engine
    # ------------------------------------------------------------------
    def calculate_segments(
        self,
        total_duration: float,
        mode: str = "fixed",
        target_duration: float = 55.0,
        min_duration: float = 15.0,
        max_duration: float = 59.0,
        safe_end_margin: float = 0.5,
        scene_timestamps: Optional[List[float]] = None,
        manual_markers: Optional[List[Tuple[float, float]]] = None,
    ) -> List[ClipSegment]:
        """Calculate ordered clip segments for a source video according to mode and constraints.

        Modes:
        - 'fixed': Fixed target duration with tail handling.
        - 'scene': Cuts aligned to detected scene boundaries within [min_duration, max_duration].
        - 'manual': User-specified (start, end) markers validated against Shorts limits.
        - 'hybrid': Initial fixed target windows snapped to nearby scene changes.
        """
        if total_duration <= 0:
            raise ValueError("Total video duration must be positive")
        if max_duration <= 0 or target_duration <= 0:
            raise ValueError("Target and max durations must be positive")
        if target_duration > max_duration:
            target_duration = max_duration

        mode = mode.lower().strip()
        raw_intervals: List[Tuple[float, float]] = []

        if mode == "manual":
            if not manual_markers:
                raise ValueError("Manual markers mode requires a non-empty list of (start, end) markers")
            for start, end in manual_markers:
                s = max(0.0, float(start))
                e = min(total_duration, float(end))
                if e <= s:
                    raise ValueError(f"Invalid manual marker range: ({s}, {e})")
                dur = e - s
                if dur > max_duration:
                    raise ValueError(f"Manual marker duration {dur:.2f}s exceeds max allowed {max_duration:.2f}s")
                raw_intervals.append((round(s, 3), round(e, 3)))

        elif mode in ("scene", "hybrid") and scene_timestamps:
            scenes = sorted(t for t in scene_timestamps if 0.0 < t < total_duration)
            cursor = 0.0
            while cursor < total_duration - 0.05:
                remaining = total_duration - cursor
                if remaining <= max_duration and (remaining <= target_duration + min_duration * 0.5 or remaining < min_duration):
                    raw_intervals.append((round(cursor, 3), round(total_duration, 3)))
                    break

                ideal_end = min(cursor + target_duration, total_duration)
                min_end = cursor + min_duration
                max_end = min(cursor + max_duration, total_duration)

                # Look for scene boundary near ideal_end within [min_end, max_end]
                candidates = [t for t in scenes if min_end <= t <= max_end]
                if candidates:
                    if mode == "hybrid":
                        # Pick closest to ideal_end
                        chosen_end = min(candidates, key=lambda t: abs(t - ideal_end))
                    else:
                        # Scene mode: pick the last scene boundary before ideal_end, or closest
                        before_ideal = [t for t in candidates if t <= ideal_end]
                        chosen_end = before_ideal[-1] if before_ideal else candidates[0]
                else:
                    chosen_end = max(min(ideal_end - safe_end_margin, max_end), min(min_end, max_end))

                if chosen_end <= cursor:
                    chosen_end = min(cursor + target_duration, total_duration)
                raw_intervals.append((round(cursor, 3), round(chosen_end, 3)))
                cursor = chosen_end

        else:
            # Default: Fixed Duration mode
            effective_step = max(min_duration * 0.5, target_duration - safe_end_margin)
            if total_duration <= max_duration and total_duration <= target_duration:
                raw_intervals.append((0.0, round(total_duration, 3)))
            else:
                cursor = 0.0
                while cursor < total_duration - 0.05:
                    remaining = total_duration - cursor
                    if remaining <= target_duration:
                        raw_intervals.append((round(cursor, 3), round(total_duration, 3)))
                        break
                    end = min(cursor + effective_step, total_duration)
                    raw_intervals.append((round(cursor, 3), round(end, 3)))
                    cursor = end

        # Post-process tail segment: avoid very short trailing clip if possible
        if len(raw_intervals) >= 2 and mode != "manual":
            last_start, last_end = raw_intervals[-1]
            last_dur = last_end - last_start
            if last_dur < min_duration:
                prev_start, _ = raw_intervals[-2]
                combined_dur = last_end - prev_start
                if combined_dur <= max_duration:
                    # Merge tail into previous segment
                    raw_intervals[-2] = (prev_start, last_end)
                    raw_intervals.pop()
                else:
                    # Rebalance last two segments evenly within max_duration
                    mid = round(prev_start + combined_dur / 2.0, 3)
                    if (mid - prev_start) <= max_duration and (last_end - mid) <= max_duration:
                        raw_intervals[-2] = (prev_start, mid)
                        raw_intervals[-1] = (mid, last_end)

        total_parts = len(raw_intervals)
        segments: List[ClipSegment] = []
        for idx, (s, e) in enumerate(raw_intervals, start=1):
            dur = round(e - s, 3)
            segments.append(
                ClipSegment(
                    sequence_number=idx,
                    total_parts=total_parts,
                    start_seconds=s,
                    end_seconds=e,
                    duration_seconds=dur,
                )
            )
        return segments

    # ------------------------------------------------------------------
    # Rendering & Visual Branding Template Application
    # ------------------------------------------------------------------
    def build_filter_complex(
        self,
        metadata: VideoMetadata,
        template: Dict[str, Any],
        part_number: int,
        total_parts: int,
        has_logo_input: bool = False,
    ) -> Tuple[str, str]:
        """Build FFmpeg -filter_complex graph for vertical 9:16 scaling, padding, logo, and non-intrusive texts."""
        target_w = int(template.get("target_width", 1080))
        target_h = int(template.get("target_height", 1920))

        # Scale preserving aspect ratio and pad onto 9:16 vertical canvas
        vf_steps = [
            f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease",
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black",
            "setsar=1",
        ]

        # Check if drawtext filter is supported in this FFmpeg build
        drawtext_supported = self._check_filter_support("drawtext")
        if drawtext_supported and template.get("enabled", True):
            font_color = template.get("font_color", "white")
            box_color = template.get("box_color", "black@0.55")
            font_size = int(template.get("font_size", 42))

            # Part badge in top-left safe zone
            if template.get("part_badge_enabled", True):
                part_txt = _escape_ffmpeg_drawtext(shape_arabic_text(f"الجزء {part_number}/{total_parts}"))
                vf_steps.append(
                    f"drawtext=text='{part_txt}':fontcolor={font_color}:fontsize={max(24, int(font_size * 0.8))}:"
                    f"box=1:boxcolor={box_color}:boxborderw=8:x=36:y=48"
                )

            # Opening text at top-center safe margin
            if template.get("opening_text_enabled", False) and template.get("opening_text"):
                open_txt = _escape_ffmpeg_drawtext(shape_arabic_text(str(template["opening_text"])))
                open_dur = float(template.get("opening_text_duration", 3.0))
                vf_steps.append(
                    f"drawtext=text='{open_txt}':fontcolor={font_color}:fontsize={font_size}:"
                    f"box=1:boxcolor={box_color}:boxborderw=10:x=(w-text_w)/2:y=120:"
                    f"enable='between(t,0,{open_dur})'"
                )

            # Subscribe reminder at bottom safe zone
            if template.get("subscribe_text_enabled", False) and template.get("subscribe_text"):
                sub_txt = _escape_ffmpeg_drawtext(shape_arabic_text(str(template["subscribe_text"])))
                sub_start = float(template.get("subscribe_text_start", 1.0))
                sub_end = sub_start + float(template.get("subscribe_text_duration", 3.5))
                vf_steps.append(
                    f"drawtext=text='{sub_txt}':fontcolor={font_color}:fontsize={max(24, int(font_size * 0.85))}:"
                    f"box=1:boxcolor={box_color}:boxborderw=10:x=(w-text_w)/2:y=h-180:"
                    f"enable='between(t,{sub_start},{sub_end})'"
                )

            # Like reminder at bottom safe zone
            if template.get("like_text_enabled", False) and template.get("like_text"):
                like_txt = _escape_ffmpeg_drawtext(shape_arabic_text(str(template["like_text"])))
                like_start = float(template.get("like_text_start", 5.0))
                like_end = like_start + float(template.get("like_text_duration", 3.5))
                vf_steps.append(
                    f"drawtext=text='{like_txt}':fontcolor={font_color}:fontsize={max(24, int(font_size * 0.85))}:"
                    f"box=1:boxcolor={box_color}:boxborderw=10:x=(w-text_w)/2:y=h-180:"
                    f"enable='between(t,{like_start},{like_end})'"
                )

        base_chain = ",".join(vf_steps)

        if has_logo_input:
            logo_scale = float(template.get("logo_scale", 0.15))
            logo_w = max(48, int(target_w * logo_scale))
            pos = template.get("logo_position", "top-right")
            if pos == "top-left":
                xy = "36:36"
            elif pos == "bottom-right":
                xy = "W-w-36:H-h-120"
            elif pos == "bottom-left":
                xy = "36:H-h-120"
            else:
                xy = "W-w-36:36"

            filter_complex = (
                f"[0:v]{base_chain}[bg];"
                f"[1:v]scale={logo_w}:-1[logo];"
                f"[bg][logo]overlay={xy}[vout]"
            )
            return filter_complex, "[vout]"
        else:
            filter_complex = f"[0:v]{base_chain}[vout]"
            return filter_complex, "[vout]"

    def _check_filter_support(self, filter_name: str) -> bool:
        try:
            res = subprocess.run(
                [self.ffmpeg_path, "-hide_banner", "-filters"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            return f" {filter_name} " in (res.stdout or "")
        except Exception:
            return False

    def render_clip(
        self,
        source_metadata: VideoMetadata,
        segment: ClipSegment,
        output_dir: Path,
        template: Optional[Dict[str, Any]] = None,
        base_prefix: str = "clip",
    ) -> ClipSegment:
        """Render a single video segment with optional branding template, logo, intro/outro, and silent audio fallback."""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        tpl = template or {"enabled": False}

        safe_prefix = sanitize_filename(base_prefix)
        out_name = f"{safe_prefix}_part{segment.sequence_number:02d}_of_{segment.total_parts:02d}.mp4"
        out_path = output_dir / out_name

        logo_path: Optional[Path] = None
        if tpl.get("enabled", True) and tpl.get("logo_enabled", False) and tpl.get("logo_path"):
            candidate_logo = Path(tpl["logo_path"])
            if candidate_logo.exists() and candidate_logo.is_file():
                logo_path = candidate_logo

        filter_complex, v_map = self.build_filter_complex(
            metadata=source_metadata,
            template=tpl,
            part_number=segment.sequence_number,
            total_parts=segment.total_parts,
            has_logo_input=(logo_path is not None),
        )

        cmd = [
            self.ffmpeg_path,
            "-y",
            "-hide_banner",
            "-ss",
            str(segment.start_seconds),
            "-t",
            str(segment.duration_seconds),
            "-i",
            str(source_metadata.filepath),
        ]

        if logo_path:
            cmd.extend(["-i", str(logo_path)])

        if not source_metadata.has_audio:
            # Generate silent audio stream so downstream players and YouTube Shorts have a standard audio track
            cmd.extend(["-f", "lavfi", "-t", str(segment.duration_seconds), "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"])
            audio_input_idx = 2 if logo_path else 1
            audio_map = f"{audio_input_idx}:a"
        else:
            audio_map = "0:a:0"

        cmd.extend(
            [
                "-filter_complex",
                filter_complex,
                "-map",
                v_map,
                "-map",
                audio_map,
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(out_path),
            ]
        )

        res = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
        if res.returncode != 0 or not out_path.exists():
            err_tail = (res.stderr or "")[-400:]
            raise RuntimeError(f"FFmpeg failed rendering segment {segment.sequence_number}: {err_tail}")

        # Optional Intro / Outro concatenation if configured and files exist
        intro_path = Path(tpl["intro_path"]) if (tpl.get("enabled") and tpl.get("intro_enabled") and tpl.get("intro_path")) else None
        outro_path = Path(tpl["outro_path"]) if (tpl.get("enabled") and tpl.get("outro_enabled") and tpl.get("outro_path")) else None
        if (intro_path and intro_path.exists()) or (outro_path and outro_path.exists()):
            out_path = self._concat_intro_outro(
                main_clip=out_path,
                intro_path=intro_path if (intro_path and intro_path.exists()) else None,
                outro_path=outro_path if (outro_path and outro_path.exists()) else None,
            )

        probed_out = self.probe_video(out_path)
        segment.output_path = out_path
        segment.duration_seconds = probed_out.duration_seconds
        segment.content_hash = compute_file_hash(out_path)
        return segment

    def _concat_intro_outro(
        self,
        main_clip: Path,
        intro_path: Optional[Path] = None,
        outro_path: Optional[Path] = None,
    ) -> Path:
        parts = []
        if intro_path:
            parts.append(intro_path)
        parts.append(main_clip)
        if outro_path:
            parts.append(outro_path)
        if len(parts) == 1:
            return main_clip

        concat_out = main_clip.with_name(f"{main_clip.stem}_branded.mp4")
        inputs = []
        filter_inputs = []
        for idx, p in enumerate(parts):
            inputs.extend(["-i", str(p)])
            filter_inputs.append(f"[{idx}:v:0][{idx}:a:0]")

        filter_str = f"{''.join(filter_inputs)}concat=n={len(parts)}:v=1:a=1[v][a]"
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-hide_banner",
            *inputs,
            "-filter_complex",
            filter_str,
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            str(concat_out),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
        if res.returncode == 0 and concat_out.exists():
            shutil.move(str(concat_out), str(main_clip))
        return main_clip

    def process_video_to_clips(
        self,
        source_path: Path | str,
        output_dir: Path | str,
        mode: str = "fixed",
        target_duration: float = 55.0,
        min_duration: float = 15.0,
        max_duration: float = 59.0,
        safe_end_margin: float = 0.5,
        template: Optional[Dict[str, Any]] = None,
        manual_markers: Optional[List[Tuple[float, float]]] = None,
    ) -> Tuple[VideoMetadata, List[ClipSegment]]:
        """Full local pipeline: validate source -> calculate segments -> render all clips with template."""
        meta = self.probe_video(source_path)
        scene_ts = None
        if mode in ("scene", "hybrid"):
            scene_ts = self.detect_scene_changes(meta.filepath)

        segments = self.calculate_segments(
            total_duration=meta.duration_seconds,
            mode=mode,
            target_duration=target_duration,
            min_duration=min_duration,
            max_duration=max_duration,
            safe_end_margin=safe_end_margin,
            scene_timestamps=scene_ts,
            manual_markers=manual_markers,
        )

        rendered: List[ClipSegment] = []
        stem = meta.filepath.stem
        for seg in segments:
            rendered_seg = self.render_clip(
                source_metadata=meta,
                segment=seg,
                output_dir=Path(output_dir),
                template=template,
                base_prefix=stem,
            )
            rendered.append(rendered_seg)

        return meta, rendered

    # ------------------------------------------------------------------
    # Test Helper: Synthetic Video & Logo Generator
    # ------------------------------------------------------------------
    def generate_test_video(
        self,
        output_path: Path | str,
        duration_seconds: float = 10.0,
        width: int = 640,
        height: int = 360,
        fps: int = 24,
        with_audio: bool = True,
    ) -> Path:
        """Generate a synthetic test video using FFmpeg lavfi testsrc (no copyrighted material)."""
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            self.ffmpeg_path,
            "-y",
            "-hide_banner",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=duration={duration_seconds}:size={width}x{height}:rate={fps}",
        ]
        if with_audio:
            cmd.extend(
                [
                    "-f",
                    "lavfi",
                    "-i",
                    f"sine=frequency=440:duration={duration_seconds}",
                    "-c:a",
                    "aac",
                ]
            )
        cmd.extend(["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(out)])
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
        if res.returncode != 0 or not out.exists():
            raise RuntimeError(f"Failed to generate synthetic test video: {res.stderr}")
        return out
