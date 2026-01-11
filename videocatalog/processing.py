"""Video processing functions for detection, splitting, and transcription."""

import json
import multiprocessing
import os
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

from .models import ClipInfo, VideoMetadata, CatalogEntry, CutCandidate


class SubprocessError(Exception):
    """Raised when a subprocess command fails."""
    pass


def _run_ffmpeg(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    """Run an ffmpeg/ffprobe command, optionally checking for errors."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SubprocessError(f"Command failed: {' '.join(cmd[:3])}...\n{result.stderr[:500]}")
    return result


def _has_content(path: Path) -> bool:
    """Check if file exists and has content."""
    try:
        return path.stat().st_size > 0
    except FileNotFoundError:
        return False


def load_catalog(output_dir: Path) -> list[CatalogEntry]:
    """Load catalog.json from output directory."""
    catalog_path = output_dir / "catalog.json"
    if not catalog_path.exists():
        return []
    try:
        data = json.loads(catalog_path.read_text())
        return [CatalogEntry(**e) for e in data.get('videos', [])]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"Warning: Failed to load catalog.json: {e}")
        return []


def save_catalog(output_dir: Path, entries: list[CatalogEntry]) -> None:
    """Save catalog.json to output directory."""
    catalog_path = output_dir / "catalog.json"
    data = {'videos': [e.model_dump() for e in entries]}
    catalog_path.write_text(json.dumps(data, indent=2))


def update_catalog(output_dir: Path, name: str, source_file: str, clip_count: int) -> None:
    """Add or update a video in the catalog."""
    entries = load_catalog(output_dir)
    entries = [e for e in entries if e.name != name]
    entries.append(CatalogEntry(
        name=name,
        source_file=source_file,
        processed_date=datetime.now().isoformat(),
        clip_count=clip_count
    ))
    save_catalog(output_dir, entries)


def detect_scenes(video_path: Path, limit: float = 0, start_time: float = 0, end_time: float = 0) -> list[tuple[float, float]]:
    """Detect scene changes using FFmpeg's scdet filter.

    Args:
        video_path: Path to video file
        limit: Duration limit (deprecated, use end_time instead)
        start_time: Start time in seconds (seek before input for speed)
        end_time: End time in seconds (0 = full video)
    """
    print("  Detecting scene changes...")
    cmd = ["ffmpeg"]
    if start_time > 0:
        cmd += ["-ss", str(start_time)]
    duration = end_time - start_time if end_time > 0 else limit
    if duration > 0:
        cmd += ["-t", str(duration)]
    cmd += [
        "-i", str(video_path),
        "-vf", "histeq,scdet=threshold=0.1",
        "-f", "null", "-"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    pattern = r"lavfi\.scd\.score:\s*([\d.]+),\s*lavfi\.scd\.time:\s*([\d.]+)"
    scenes = []
    for match in re.finditer(pattern, result.stderr):
        score = float(match.group(1))
        time = float(match.group(2)) + start_time  # Convert to absolute time
        if score >= 5:
            scenes.append((time, score))

    return scenes


def detect_black_frames(video_path: Path, limit: float = 0, start_time: float = 0, end_time: float = 0) -> list[tuple[float, float]]:
    """Detect black frames using FFmpeg's blackdetect filter.

    Args:
        video_path: Path to video file
        limit: Duration limit (deprecated, use end_time instead)
        start_time: Start time in seconds (seek before input for speed)
        end_time: End time in seconds (0 = full video)
    """
    print("  Detecting black frames...")
    cmd = ["ffmpeg"]
    if start_time > 0:
        cmd += ["-ss", str(start_time)]
    duration = end_time - start_time if end_time > 0 else limit
    if duration > 0:
        cmd += ["-t", str(duration)]
    cmd += [
        "-i", str(video_path),
        "-vf", "blackdetect=d=0.1:pix_th=0.10",
        "-an", "-f", "null", "-"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    pattern = r"black_start:([\d.]+)\s+black_end:([\d.]+)\s+black_duration:([\d.]+)"
    blacks = []
    for match in re.finditer(pattern, result.stderr):
        black_end = float(match.group(2)) + start_time  # Convert to absolute time
        black_duration = float(match.group(3))
        if black_duration >= 0.1:
            blacks.append((black_end, black_duration))

    return blacks


def detect_audio_changes(video_path: Path, duration: float, limit: float = 0, start_time: float = 0, end_time: float = 0) -> dict[int, float]:
    """Compute per-second RMS levels and detect large changes.

    Args:
        video_path: Path to video file
        duration: Total video duration (used for progress, not limiting)
        limit: Duration limit (deprecated, use end_time instead)
        start_time: Start time in seconds (seek before input for speed)
        end_time: End time in seconds (0 = full video)
    """
    print("  Analyzing audio levels...")
    rms_file = Path(tempfile.gettempdir()) / f"rms_analysis_{os.getpid()}.txt"

    cmd = ["ffmpeg"]
    if start_time > 0:
        cmd += ["-ss", str(start_time)]
    segment_duration = end_time - start_time if end_time > 0 else limit
    if segment_duration > 0:
        cmd += ["-t", str(segment_duration)]
    cmd += [
        "-i", str(video_path),
        "-af", f"asetnsamples=n=48000,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file={rms_file}",
        "-f", "null", "-"
    ]
    subprocess.run(cmd, capture_output=True, text=True)

    rms = {}
    try:
        if rms_file.exists():
            content = rms_file.read_text()
            pattern = r"pts_time:(\d+)\s*\n.*?RMS_level=([-\d.inf]+)"
            for match in re.finditer(pattern, content):
                t = int(match.group(1)) + int(start_time)  # Convert to absolute time
                level_str = match.group(2)
                if level_str == '-inf' or level_str == '-':
                    continue
                level = float(level_str)
                rms[t] = level
    finally:
        rms_file.unlink(missing_ok=True)

    changes = {}
    sorted_times = sorted(rms.keys())
    for i in range(1, len(sorted_times)):
        t = sorted_times[i]
        prev_t = sorted_times[i - 1]
        step = abs(rms[t] - rms[prev_t])
        if step > 5:
            changes[t] = step

    return changes


def get_video_duration(video_path: Path) -> float:
    """Get video duration in seconds."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(video_path)
    ]
    result = _run_ffmpeg(cmd, check=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise SubprocessError(f"Invalid duration from ffprobe: {result.stdout!r}")


def format_time(seconds: float) -> str:
    """Format seconds as HH:MM:SS.mmm"""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def format_duration(seconds: float) -> str:
    """Format seconds as MM:SS or HH:MM:SS."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_time_filename(seconds: float) -> str:
    """Format seconds as filename-safe timestamp like 00h00m00s (always sortable)."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    return f"{hours:02d}h{minutes:02d}m{secs:02d}s"


def verify_scene_change(video_path: Path, time: float, threshold: float = 0.7) -> tuple[bool, float]:
    """Verify scene change by comparing color histograms before/after.

    Uses histogram comparison which is robust to camera motion.
    Returns (is_valid, similarity). A real scene change has low similarity.
    A smooth transition (same scene) has high similarity.
    """
    import cv2

    before_time = max(0, time - 0.5)
    after_time = time + 0.5

    # Extract two frames to temp files
    tmp_dir = Path(tempfile.gettempdir())
    frame1 = tmp_dir / f"hist_before_{os.getpid()}.png"
    frame2 = tmp_dir / f"hist_after_{os.getpid()}.png"

    try:
        # Extract frame before
        subprocess.run([
            "ffmpeg", "-y", "-ss", str(before_time), "-i", str(video_path),
            "-frames:v", "1", "-f", "image2", str(frame1)
        ], capture_output=True)

        # Extract frame after
        subprocess.run([
            "ffmpeg", "-y", "-ss", str(after_time), "-i", str(video_path),
            "-frames:v", "1", "-f", "image2", str(frame2)
        ], capture_output=True)

        if not frame1.exists() or not frame2.exists():
            return True, 0.0

        # Load images and convert to HSV
        img1 = cv2.imread(str(frame1))
        img2 = cv2.imread(str(frame2))
        if img1 is None or img2 is None:
            return True, 0.0

        hsv1 = cv2.cvtColor(img1, cv2.COLOR_BGR2HSV)
        hsv2 = cv2.cvtColor(img2, cv2.COLOR_BGR2HSV)

        # Calculate and normalize histograms
        hist1 = cv2.calcHist([hsv1], [0, 1], None, [50, 60], [0, 180, 0, 256])
        hist2 = cv2.calcHist([hsv2], [0, 1], None, [50, 60], [0, 180, 0, 256])
        cv2.normalize(hist1, hist1)
        cv2.normalize(hist2, hist2)

        # Compare histograms (1.0 = identical, 0 = no correlation)
        similarity = cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)

        # High similarity = same scene = should NOT cut (false positive)
        # Low similarity = different scene = real cut
        return similarity < threshold, similarity

    finally:
        frame1.unlink(missing_ok=True)
        frame2.unlink(missing_ok=True)


def check_scene_stability(
    video_path: Path,
    time: float,
    threshold: float = 0.955
) -> tuple[bool, float, float]:
    """Check if distant frames before/after cut are similar (flash detection).

    Compares frames at ±0.5s and ±2s. If BOTH intervals show high similarity
    (>=0.9), the cut is likely a flash/disturbance.

    Returns (is_flash, sim_1s, sim_2s).
    """
    import cv2

    def compare_frames(before_time: float, after_time: float) -> float:
        tmp_dir = Path(tempfile.gettempdir())
        frame1 = tmp_dir / f"stab_before_{os.getpid()}.png"
        frame2 = tmp_dir / f"stab_after_{os.getpid()}.png"

        try:
            subprocess.run([
                "ffmpeg", "-y", "-ss", str(max(0, before_time)), "-i", str(video_path),
                "-frames:v", "1", "-f", "image2", str(frame1)
            ], capture_output=True)
            subprocess.run([
                "ffmpeg", "-y", "-ss", str(after_time), "-i", str(video_path),
                "-frames:v", "1", "-f", "image2", str(frame2)
            ], capture_output=True)

            if not frame1.exists() or not frame2.exists():
                return 0.0

            img1 = cv2.imread(str(frame1))
            img2 = cv2.imread(str(frame2))
            if img1 is None or img2 is None:
                return 0.0

            hsv1 = cv2.cvtColor(img1, cv2.COLOR_BGR2HSV)
            hsv2 = cv2.cvtColor(img2, cv2.COLOR_BGR2HSV)
            hist1 = cv2.calcHist([hsv1], [0, 1], None, [50, 60], [0, 180, 0, 256])
            hist2 = cv2.calcHist([hsv2], [0, 1], None, [50, 60], [0, 180, 0, 256])
            cv2.normalize(hist1, hist1)
            cv2.normalize(hist2, hist2)

            return cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)
        finally:
            frame1.unlink(missing_ok=True)
            frame2.unlink(missing_ok=True)

    # Check at multiple intervals to catch both momentary flashes and sustained flickering
    sim_short = compare_frames(time - 0.5, time + 0.5)
    sim_long = compare_frames(time - 2.0, time + 2.0)

    # Flash if EITHER interval is very high AND both are at least moderately high
    # This avoids filtering real cuts that have one high but one low interval
    is_flash = (sim_short >= threshold or sim_long >= threshold) and min(sim_short, sim_long) >= 0.85
    return is_flash, sim_short, sim_long


def verify_candidates(
    video_path: Path,
    candidates: list[CutCandidate],
    scene_max_scores: dict[int, float],
    threshold: float = 0.7,
    verbose: bool = False
) -> list[CutCandidate]:
    """Filter candidates by verifying with histogram comparison and flash detection.

    - Black frame corroboration: pass without checks (strong signal)
    - Borderline (max_score < 10): histogram verification first
    - All others: flash detection via stability check
    """
    if verbose:
        print(f"  Verifying {len(candidates)} candidates with histogram comparison...")

    verified = []
    for c in candidates:
        max_score = scene_max_scores.get(int(c.time), 0)
        has_black = c.black_duration >= 0.2
        has_audio = c.audio_step >= 5

        # Black or audio corroboration = likely real cut, skip flash detection
        if has_black or has_audio:
            if verbose:
                signal = "black frame" if has_black else "audio"
                print(f"    {format_time(c.time)} max={max_score:.1f} -> PASS ({signal})")
            verified.append(c)
            continue

        # Borderline detections need histogram check first
        if max_score < 10:
            is_valid, similarity = verify_scene_change(video_path, c.time, threshold)
            if not is_valid:
                if verbose:
                    print(f"    {format_time(c.time)} max={max_score:.1f} hist={similarity:.3f} -> FAIL (same scene)")
                continue

        # Flash detection for scene-only candidates (no black/audio corroboration)
        is_flash, sim_short, sim_long = check_scene_stability(video_path, c.time)
        if is_flash:
            if verbose:
                print(f"    {format_time(c.time)} max={max_score:.1f} stab={sim_short:.2f}/{sim_long:.2f} -> FAIL (flash)")
            continue

        if verbose:
            print(f"    {format_time(c.time)} max={max_score:.1f} stab={sim_short:.2f}/{sim_long:.2f} -> PASS")
        verified.append(c)

    if verbose:
        print(f"  Verified: {len(verified)}/{len(candidates)} candidates passed")

    return verified


def find_cuts(
    scenes: list[tuple[float, float]],
    blacks: list[tuple[float, float]],
    audio_changes: dict[int, float],
    min_confidence: int,
    min_gap: float = 10.0,
    window: int = 2,
    return_all: bool = False
) -> list[CutCandidate] | tuple[list[CutCandidate], list[CutCandidate]]:
    """Combine signals to find recording boundaries.

    Uses cluster totals: sum of all scene scores in same second.
    This better identifies recording boundaries which often have
    multiple rapid detections.
    """
    from collections import defaultdict

    # Group scenes by second - keep all detections for cluster analysis
    scene_clusters: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for time, score in scenes:
        t = int(time)
        scene_clusters[t].append((time, score))

    # Compute cluster totals, max scores, and best time for each second
    scene_totals: dict[int, float] = {}
    scene_max: dict[int, float] = {}
    scene_best_time: dict[int, float] = {}
    for t, detections in scene_clusters.items():
        scene_totals[t] = sum(score for _, score in detections)
        # Use time of highest-scoring detection as the cut point
        best = max(detections, key=lambda x: x[1])
        scene_max[t] = best[1]
        scene_best_time[t] = best[0]

    black_map: dict[int, tuple[float, float]] = {}
    for end_time, duration in blacks:
        t = int(end_time)
        if t not in black_map or duration > black_map[t][1]:
            black_map[t] = (end_time, duration)

    # Build candidates from scene clusters (not from window expansion)
    candidates: list[CutCandidate] = []

    for t in scene_totals:
        cluster_total = scene_totals[t]
        max_score = scene_max[t]

        # Require at least one strong detection in the cluster
        # This filters clusters of many weak detections (false positives)
        # Threshold 8 separates true cuts (min 8.3) from false positives (max 7.7)
        if max_score < 8:
            continue

        # Look for corroborating signals in nearby seconds
        best_black_duration = 0.0
        best_audio_step = 0.0

        for offset in range(-window, window + 1):
            check_t = t + offset
            if check_t in black_map:
                _, duration = black_map[check_t]
                if duration > best_black_duration:
                    best_black_duration = duration
            if check_t in audio_changes:
                if audio_changes[check_t] > best_audio_step:
                    best_audio_step = audio_changes[check_t]

        # Only apply audio bonus if scene is strong (max >= 10)
        # For borderline detections, audio often indicates noise not confirmation
        effective_audio = best_audio_step if max_score >= 10 else 0.0

        candidate = CutCandidate(
            time=scene_best_time[t],
            scene_score=cluster_total,  # Use cluster total as score
            black_duration=best_black_duration,
            audio_step=effective_audio
        )
        candidates.append(candidate)

    # Also add audio-only candidates (significant audio change without scene)
    for t, step in audio_changes.items():
        if step >= 15 and t not in scene_totals:
            candidate = CutCandidate(
                time=float(t),
                scene_score=0.0,
                black_duration=black_map.get(t, (0, 0))[1] if t in black_map else 0.0,
                audio_step=step
            )
            candidates.append(candidate)

    # Sort by confidence score (highest first) for greedy selection
    sorted_candidates = sorted(candidates, key=lambda c: -c.confidence_score)
    selected = []

    for candidate in sorted_candidates:
        if candidate.confidence_score < min_confidence:
            continue

        too_close = False
        for existing in selected:
            if abs(candidate.time - existing.time) < min_gap:
                too_close = True
                break

        if not too_close:
            selected.append(candidate)

    result = sorted(selected, key=lambda c: c.time)
    if return_all:
        return result, candidates, scene_max
    return result


def split_video(
    video_path: Path,
    output_dir: Path,
    cuts: list[CutCandidate],
    duration: float
) -> list[Path]:
    """Split video at cut boundaries, transcoding to MP4."""
    output_dir.mkdir(parents=True, exist_ok=True)

    stem = video_path.stem
    boundaries = [0.0] + [c.time for c in cuts] + [duration]
    output_files = []

    for i in range(len(boundaries) - 1):
        start = boundaries[i]
        end = boundaries[i + 1]
        segment_num = i + 1
        time_stamp = format_time_filename(start)

        output_path = output_dir / f"{stem}_{time_stamp}.mp4"
        output_files.append(output_path)

        print(f"  Segment {segment_num}: {format_time(start)} -> {format_time(end)} => {output_path.name}")

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", str(video_path),
            "-t", str(end - start),
            "-vf", "yadif,hqdn3d",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "22",
            "-c:a", "aac",
            "-b:a", "128k",
            str(output_path)
        ]

        _run_ffmpeg(cmd, check=True)

    return output_files


def convert_to_mp4(video_path: Path) -> Path:
    """Convert video to MP4 if not already."""
    if video_path.suffix.lower() == '.mp4':
        return video_path

    mp4_path = video_path.with_suffix('.mp4')
    if mp4_path.exists():
        return mp4_path

    print(f"    Converting to MP4...")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", "yadif,hqdn3d",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "22",
        "-c:a", "aac",
        "-b:a", "128k",
        str(mp4_path)
    ]
    _run_ffmpeg(cmd, check=True)
    return mp4_path


def generate_thumbnails(video_path: Path, thumb_dir: Path, count: int = 12) -> list[str]:
    """Generate multiple thumbnails from video, always including first and last frame."""
    duration = get_video_duration(video_path)
    thumbs = []

    # Generate seek times: first frame, evenly spaced middle frames, last frame
    seek_times = [0.0]  # First frame
    if count > 2:
        for i in range(1, count - 1):
            seek_times.append(duration * i / (count - 1))
    if count > 1:
        seek_times.append(max(0, duration - 0.1))  # Last frame

    for i, seek in enumerate(seek_times):
        thumb_name = f"{video_path.stem}_{i}.jpg"
        thumb_path = thumb_dir / thumb_name

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(seek),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "3",
            str(thumb_path)
        ]
        subprocess.run(cmd, capture_output=True)
        thumbs.append(thumb_name)

    return thumbs


SPRITE_THUMB_W, SPRITE_THUMB_H = 320, 180
SPRITE_GAP = 2


def create_sprite(thumb_dir: Path, thumb_names: list[str], video_stem: str) -> str | None:
    """Create sprite from thumbnails and delete originals. Returns sprite filename."""
    if not thumb_names:
        return None

    cols, rows = 4, 3
    sprite_w = SPRITE_THUMB_W * cols + SPRITE_GAP * (cols - 1)
    sprite_h = SPRITE_THUMB_H * rows + SPRITE_GAP * (rows - 1)
    sprite = Image.new('RGBA', (sprite_w, sprite_h), (0, 0, 0, 0))

    for i, thumb_name in enumerate(thumb_names):
        thumb_path = thumb_dir / thumb_name
        if not thumb_path.exists():
            continue
        col, row = i % cols, i // cols
        x = col * (SPRITE_THUMB_W + SPRITE_GAP)
        y = row * (SPRITE_THUMB_H + SPRITE_GAP)
        img = ImageOps.fit(Image.open(thumb_path), (SPRITE_THUMB_W, SPRITE_THUMB_H), Image.LANCZOS)
        sprite.paste(img, (x, y))
        thumb_path.unlink()

    sprite_name = f"{video_stem}_sprite.webp"
    sprite.save(thumb_dir / sprite_name, 'WEBP', quality=85)
    return sprite_name


_whisper_model = None


def get_whisper_model():
    """Get or create the Whisper model (singleton per process)."""
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        print(f"  [pid {os.getpid()}] Loading Whisper large-v3 model...")
        _whisper_model = WhisperModel("large-v3", device="auto", compute_type="auto")
    return _whisper_model


def extract_audio(video_path: Path) -> Path:
    """Extract audio from video to WAV for cleaner transcription."""
    wav_path = video_path.with_suffix('.wav')
    if wav_path.exists():
        return wav_path

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(wav_path)
    ]
    subprocess.run(cmd, capture_output=True)
    return wav_path


def transcribe_video(video_path: Path) -> str:
    """Transcribe video audio using Whisper."""
    txt_path = video_path.with_suffix('.txt')

    if _has_content(txt_path):
        return txt_path.read_text()

    try:
        wav_path = extract_audio(video_path)
        model = get_whisper_model()

        segments, _ = model.transcribe(
            str(wav_path),
            language="no",
            beam_size=10,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500}
        )

        text = " ".join(seg.text.strip() for seg in segments)
        txt_path.write_text(text)
        wav_path.unlink(missing_ok=True)

        return text
    except Exception as e:
        print(f"    Error transcribing: {e}")
        return ""


def _transcribe_from_wav(video_path: Path, wav_path: Path) -> str:
    """Transcribe from pre-extracted WAV file."""
    txt_path = video_path.with_suffix('.txt')

    if _has_content(txt_path):
        wav_path.unlink(missing_ok=True)
        return txt_path.read_text()

    try:
        model = get_whisper_model()
        segments, _ = model.transcribe(
            str(wav_path),
            language="no",
            beam_size=10,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500}
        )
        text = " ".join(seg.text.strip() for seg in segments)
        txt_path.write_text(text)
        return text
    except Exception as e:
        print(f"    Error transcribing {video_path.name}: {e}")
        return ""
    finally:
        wav_path.unlink(missing_ok=True)


def _transcribe_worker(args: tuple[str, str]) -> tuple[str, str]:
    """Worker function for multiprocessing pool. Takes/returns strings for pickling."""
    video_path_str, wav_path_str = args
    video_path = Path(video_path_str)
    wav_path = Path(wav_path_str)
    transcript = _transcribe_from_wav(video_path, wav_path)
    return (video_path_str, transcript)


def _get_default_workers() -> int:
    """Get default worker count for ffmpeg operations."""
    return min(os.cpu_count() or 4, 8)


def process_clips(video_subdir: Path, video_files: list[Path], transcribe: bool = True, workers: int = 0, transcribe_workers: int = 1) -> list[ClipInfo]:
    """Process clips with parallel audio extraction and thumbnails."""
    thumb_dir = video_subdir / "thumbs"
    thumb_dir.mkdir(exist_ok=True)

    if workers <= 0:
        workers = _get_default_workers()

    print(f"Processing {len(video_files)} clips (workers={workers})...")

    # Phase 1: Convert to MP4 if needed (parallel)
    non_mp4 = [(i, v) for i, v in enumerate(video_files) if v.suffix.lower() != '.mp4']
    mp4_results = {}  # index -> mp4_path
    if non_mp4:
        print(f"  Converting {len(non_mp4)} non-MP4 files...")
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(convert_to_mp4, v): i for i, v in non_mp4}
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    mp4_results[idx] = future.result()
                except Exception as e:
                    print(f"    Error converting {video_files[idx].name}: {e}")
                    mp4_results[idx] = video_files[idx]  # keep original on error
    # Build mp4_files list preserving order
    mp4_files = [mp4_results.get(i, v if v.suffix.lower() == '.mp4' else v.with_suffix('.mp4'))
                 for i, v in enumerate(video_files)]

    # Phase 2: Extract audio in parallel (for files needing transcription)
    wav_map = {}  # mp4_path -> wav_path
    if transcribe:
        to_transcribe = [f for f in mp4_files if not f.with_suffix('.txt').exists()]
        if to_transcribe:
            print(f"  Extracting audio for {len(to_transcribe)} files...")
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(extract_audio, f): f for f in to_transcribe}
                for future in as_completed(futures):
                    mp4 = futures[future]
                    try:
                        wav_map[mp4] = future.result()
                    except Exception as e:
                        print(f"    Error extracting audio for {mp4.name}: {e}")

    # Phase 3: Transcribe (parallel with multiprocessing if transcribe_workers > 1)
    transcripts = {}
    if transcribe and wav_map:
        print(f"  Transcribing {len(wav_map)} files ({transcribe_workers} workers)...")
        try:
            if transcribe_workers == 1:
                # Sequential - no multiprocessing overhead
                for i, (mp4, wav) in enumerate(wav_map.items(), 1):
                    print(f"    [{i}/{len(wav_map)}] {mp4.name}")
                    transcripts[mp4] = _transcribe_from_wav(mp4, wav)
            else:
                # Parallel - each process loads own model
                work_items = [(str(mp4), str(wav)) for mp4, wav in wav_map.items()]
                ctx = multiprocessing.get_context('spawn')
                pool = ctx.Pool(processes=transcribe_workers)
                try:
                    for i, (video_path_str, transcript) in enumerate(pool.imap_unordered(_transcribe_worker, work_items), 1):
                        print(f"    [{i}/{len(wav_map)}] {Path(video_path_str).name}")
                        transcripts[Path(video_path_str)] = transcript
                finally:
                    pool.close()
                    pool.join()
        except Exception:
            # Clean up WAV files on error
            for wav in wav_map.values():
                wav.unlink(missing_ok=True)
            raise

    # Phase 4: Generate thumbnails and sprites in parallel
    print("  Generating thumbnails...")
    thumb_results = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(generate_thumbnails, f, thumb_dir): f for f in mp4_files}
        for future in as_completed(futures):
            mp4 = futures[future]
            try:
                thumb_results[mp4] = future.result()
            except Exception as e:
                print(f"    Error generating thumbnails for {mp4.name}: {e}")
                thumb_results[mp4] = []

    # Create sprites from thumbnails (deletes individual thumbs)
    print("  Creating sprites...")
    sprite_results = {}
    for mp4_file in mp4_files:
        thumbs = thumb_results.get(mp4_file, [])
        sprite_results[mp4_file] = create_sprite(thumb_dir, thumbs, mp4_file.stem)

    # Build final clip list (preserve original order)
    clips = []
    for mp4_file in mp4_files:
        txt_path = mp4_file.with_suffix('.txt')
        if mp4_file in transcripts:
            transcript = transcripts[mp4_file]
        elif _has_content(txt_path):
            transcript = txt_path.read_text()
        else:
            transcript = ""

        duration = get_video_duration(mp4_file)
        sprite = sprite_results.get(mp4_file)

        clips.append(ClipInfo(
            file=mp4_file.name,
            name=mp4_file.stem,
            thumbs=[],
            sprite=f"thumbs/{sprite}" if sprite else None,
            duration=format_duration(duration),
            transcript=transcript
        ))

    return clips
