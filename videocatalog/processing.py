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


def detect_scenes(video_path: Path) -> list[tuple[float, float]]:
    """Detect scene changes using FFmpeg's scdet filter."""
    print("  Detecting scene changes...")
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vf", "scdet=threshold=0.1",
        "-f", "null", "-"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    pattern = r"lavfi\.scd\.score:\s*([\d.]+),\s*lavfi\.scd\.time:\s*([\d.]+)"
    scenes = []
    for match in re.finditer(pattern, result.stderr):
        score = float(match.group(1))
        time = float(match.group(2))
        if score >= 5:
            scenes.append((time, score))

    return scenes


def detect_black_frames(video_path: Path) -> list[tuple[float, float]]:
    """Detect black frames using FFmpeg's blackdetect filter."""
    print("  Detecting black frames...")
    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-vf", "blackdetect=d=0.1:pix_th=0.10",
        "-an", "-f", "null", "-"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)

    pattern = r"black_start:([\d.]+)\s+black_end:([\d.]+)\s+black_duration:([\d.]+)"
    blacks = []
    for match in re.finditer(pattern, result.stderr):
        end_time = float(match.group(2))
        duration = float(match.group(3))
        if duration >= 0.1:
            blacks.append((end_time, duration))

    return blacks


def detect_audio_changes(video_path: Path, duration: float) -> dict[int, float]:
    """Compute per-second RMS levels and detect large changes."""
    print("  Analyzing audio levels...")
    rms_file = Path(tempfile.gettempdir()) / f"rms_analysis_{os.getpid()}.txt"

    cmd = [
        "ffmpeg", "-i", str(video_path),
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
                t = int(match.group(1))
                level_str = match.group(2)
                if level_str == '-inf' or level_str == '-':
                    continue
                level = float(level_str)
                rms[t] = level
    finally:
        rms_file.unlink(missing_ok=True)

    changes = {}
    for t in range(1, int(duration) + 1):
        if t in rms and t - 1 in rms:
            step = abs(rms[t] - rms[t - 1])
            if step > 10:
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


def find_cuts(
    scenes: list[tuple[float, float]],
    blacks: list[tuple[float, float]],
    audio_changes: dict[int, float],
    min_confidence: int,
    min_gap: float = 10.0,
    window: int = 3
) -> list[CutCandidate]:
    """Combine signals to find recording boundaries."""
    scene_map: dict[int, tuple[float, float]] = {}
    for time, score in scenes:
        t = int(time)
        if t not in scene_map or score > scene_map[t][1]:
            scene_map[t] = (time, score)

    black_map: dict[int, tuple[float, float]] = {}
    for end_time, duration in blacks:
        t = int(end_time)
        if t not in black_map or duration > black_map[t][1]:
            black_map[t] = (end_time, duration)

    potential_times = set()
    for t, (_, score) in scene_map.items():
        if score >= 5:
            potential_times.add(t)
    for t in black_map:
        potential_times.add(t)
    for t, step in audio_changes.items():
        if step >= 12:
            potential_times.add(t)

    candidates: list[CutCandidate] = []

    for t in potential_times:
        best_scene_score = 0.0
        best_scene_time = float(t)
        best_black_duration = 0.0
        best_audio_step = 0.0

        for offset in range(-window, window + 1):
            check_t = t + offset

            if check_t in scene_map:
                time, score = scene_map[check_t]
                if score > best_scene_score:
                    best_scene_score = score
                    best_scene_time = time

            if check_t in black_map:
                _, duration = black_map[check_t]
                if duration > best_black_duration:
                    best_black_duration = duration

            if check_t in audio_changes:
                if audio_changes[check_t] > best_audio_step:
                    best_audio_step = audio_changes[check_t]

        candidate = CutCandidate(
            time=best_scene_time,
            scene_score=best_scene_score,
            black_duration=best_black_duration,
            audio_step=best_audio_step
        )
        candidates.append(candidate)

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

    return sorted(selected, key=lambda c: c.time)


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

        output_path = output_dir / f"{stem}_{segment_num:03d}.mp4"
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
    """Generate multiple thumbnails from video."""
    duration = get_video_duration(video_path)
    thumbs = []

    for i in range(count):
        pct = (i + 0.5) / count
        seek = duration * pct
        seek = max(0, min(seek, duration - 0.1))

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

    # Phase 4: Generate thumbnails in parallel
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
        thumbs = thumb_results.get(mp4_file, [])

        clips.append(ClipInfo(
            file=mp4_file.name,
            name=mp4_file.stem,
            thumbs=[f"thumbs/{t}" for t in thumbs],
            duration=format_duration(duration),
            transcript=transcript
        ))

    return clips
