"""Video processing functions for detection, splitting, and transcription."""

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from .models import ClipInfo, VideoMetadata, CatalogEntry, CutCandidate


def load_catalog(output_dir: Path) -> list[CatalogEntry]:
    """Load catalog.json from output directory."""
    catalog_path = output_dir / "catalog.json"
    if not catalog_path.exists():
        return []
    data = json.loads(catalog_path.read_text())
    return [CatalogEntry(**e) for e in data.get('videos', [])]


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
    rms_file = Path("/tmp/rms_analysis.txt")

    cmd = [
        "ffmpeg", "-i", str(video_path),
        "-af", f"asetnsamples=n=48000,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file={rms_file}",
        "-f", "null", "-"
    ]
    subprocess.run(cmd, capture_output=True, text=True)

    rms = {}
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
    result = subprocess.run(cmd, capture_output=True, text=True)
    return float(result.stdout.strip())


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

        subprocess.run(cmd, capture_output=True)

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
    subprocess.run(cmd, capture_output=True)
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
    """Get or create the Whisper model (singleton)."""
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        print("  Loading Whisper large-v3 model...")
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

    if txt_path.exists() and txt_path.stat().st_size > 0:
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


def process_clips(video_subdir: Path, video_files: list[Path], transcribe: bool = True) -> list[ClipInfo]:
    """Process clips: generate thumbnails and transcripts."""
    thumb_dir = video_subdir / "thumbs"
    thumb_dir.mkdir(exist_ok=True)

    print("Processing clips...")
    clips = []

    for i, video in enumerate(video_files, 1):
        mp4_file = convert_to_mp4(video)
        print(f"  [{i}/{len(video_files)}] {mp4_file.name}")

        duration = get_video_duration(mp4_file)
        thumbs = generate_thumbnails(mp4_file, thumb_dir)

        txt_path = mp4_file.with_suffix('.txt')
        if txt_path.exists() and txt_path.stat().st_size > 0:
            transcript = txt_path.read_text()
        elif transcribe:
            print(f"    Transcribing...")
            transcript = transcribe_video(mp4_file)
        else:
            transcript = ""

        clips.append(ClipInfo(
            file=mp4_file.name,
            name=mp4_file.stem,
            thumbs=[f"thumbs/{t}" for t in thumbs],
            duration=format_duration(duration),
            transcript=transcript
        ))

    return clips
