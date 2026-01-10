#!/usr/bin/env python3
"""Split a video file at recording boundaries using multi-signal detection."""

import argparse
import html as html_lib
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class ClipInfo:
    """Metadata for a single video clip."""
    file: str
    name: str
    thumbs: list[str]
    duration: str
    transcript: str


@dataclass
class VideoMetadata:
    """Metadata for a processed source video."""
    source_file: str
    processed_date: str
    clips: list[ClipInfo] = field(default_factory=list)

    def save(self, path: Path) -> None:
        data = {
            'source_file': self.source_file,
            'processed_date': self.processed_date,
            'clips': [asdict(c) for c in self.clips]
        }
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path) -> 'VideoMetadata':
        data = json.loads(path.read_text())
        clips = [ClipInfo(**c) for c in data.get('clips', [])]
        return cls(
            source_file=data['source_file'],
            processed_date=data['processed_date'],
            clips=clips
        )


@dataclass
class CatalogEntry:
    """Entry in the master catalog."""
    name: str
    source_file: str
    processed_date: str
    clip_count: int


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
    data = {'videos': [asdict(e) for e in entries]}
    catalog_path.write_text(json.dumps(data, indent=2))


def update_catalog(output_dir: Path, name: str, source_file: str, clip_count: int) -> None:
    """Add or update a video in the catalog."""
    entries = load_catalog(output_dir)
    # Remove existing entry with same name
    entries = [e for e in entries if e.name != name]
    # Add new entry
    entries.append(CatalogEntry(
        name=name,
        source_file=source_file,
        processed_date=datetime.now().isoformat(),
        clip_count=clip_count
    ))
    save_catalog(output_dir, entries)


@dataclass
class CutCandidate:
    time: float
    scene_score: float = 0.0
    black_duration: float = 0.0
    audio_step: float = 0.0

    @property
    def confidence_score(self) -> int:
        """Calculate confidence score based on multiple signals."""
        score = 0

        # Scene detection scoring
        if self.scene_score >= 25:
            score += 40
        elif self.scene_score >= 15:
            score += 25
        elif self.scene_score >= 10:
            score += 15
        elif self.scene_score >= 6:
            score += 5

        # Black frame scoring
        if self.black_duration >= 1.0:
            score += 35
        elif self.black_duration >= 0.5:
            score += 25
        elif self.black_duration >= 0.2:
            score += 15

        # Audio RMS step scoring
        if self.audio_step >= 25:
            score += 30
        elif self.audio_step >= 18:
            score += 20
        elif self.audio_step >= 12:
            score += 10

        return score

    def signal_summary(self) -> str:
        parts = []
        if self.scene_score > 0:
            parts.append(f"scene:{self.scene_score:.1f}")
        if self.black_duration > 0:
            parts.append(f"black:{self.black_duration:.2f}s")
        if self.audio_step > 0:
            parts.append(f"audio:{self.audio_step:.1f}dB")
        return " ".join(parts) if parts else "none"


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
        if score >= 5:  # Low threshold, we'll filter later
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

    # Parse RMS values
    rms = {}
    if rms_file.exists():
        content = rms_file.read_text()
        pattern = r"pts_time:(\d+)\s*\n.*?RMS_level=([-\d.inf]+)"
        for match in re.finditer(pattern, content):
            t = int(match.group(1))
            level_str = match.group(2)
            if level_str == '-inf' or level_str == '-':
                continue  # Skip silent sections
            level = float(level_str)
            rms[t] = level

    # Calculate step changes
    changes = {}
    for t in range(1, int(duration) + 1):
        if t in rms and t - 1 in rms:
            step = abs(rms[t] - rms[t - 1])
            if step > 10:  # Only track significant changes
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


def find_cuts(
    scenes: list[tuple[float, float]],
    blacks: list[tuple[float, float]],
    audio_changes: dict[int, float],
    min_confidence: int,
    min_gap: float = 10.0,
    window: int = 3
) -> list[CutCandidate]:
    """Combine signals to find recording boundaries.

    Uses a sliding window to aggregate signals from nearby seconds.
    """
    # Build raw signal maps
    scene_map: dict[int, tuple[float, float]] = {}  # second -> (time, score)
    for time, score in scenes:
        t = int(time)
        if t not in scene_map or score > scene_map[t][1]:
            scene_map[t] = (time, score)

    black_map: dict[int, tuple[float, float]] = {}  # second -> (time, duration)
    for end_time, duration in blacks:
        t = int(end_time)
        if t not in black_map or duration > black_map[t][1]:
            black_map[t] = (end_time, duration)

    # Find all potential cut points (any second with a significant signal)
    potential_times = set()
    for t, (_, score) in scene_map.items():
        if score >= 5:
            potential_times.add(t)
    for t in black_map:
        potential_times.add(t)
    for t, step in audio_changes.items():
        if step >= 12:
            potential_times.add(t)

    # Build candidates with windowed aggregation
    candidates: list[CutCandidate] = []

    for t in potential_times:
        # Aggregate signals from window around this time
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

    # Filter by confidence and minimum gap
    sorted_candidates = sorted(candidates, key=lambda c: -c.confidence_score)
    selected = []

    for candidate in sorted_candidates:
        if candidate.confidence_score < min_confidence:
            continue

        # Check minimum gap from already selected cuts
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
    """Split video at cut boundaries, transcoding to MP4. Returns list of output files."""
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
            "-vf", "yadif,hqdn3d",  # Deinterlace and denoise
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
    """Convert video to MP4 if not already. Returns path to MP4 file."""
    if video_path.suffix.lower() == '.mp4':
        return video_path

    mp4_path = video_path.with_suffix('.mp4')
    if mp4_path.exists():
        return mp4_path

    print(f"    Converting to MP4...")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vf", "yadif,hqdn3d",  # Deinterlace and denoise
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
    """Generate multiple thumbnails from video. Returns list of thumb filenames."""
    duration = get_video_duration(video_path)
    thumbs = []

    for i in range(count):
        # Spread thumbnails evenly across video
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


def format_duration(seconds: float) -> str:
    """Format seconds as MM:SS or HH:MM:SS."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    if hours > 0:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


# Global model instance to avoid reloading
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
        "-vn",  # No video
        "-acodec", "pcm_s16le",  # 16-bit PCM
        "-ar", "16000",  # 16kHz sample rate (Whisper native)
        "-ac", "1",  # Mono
        str(wav_path)
    ]
    subprocess.run(cmd, capture_output=True)
    return wav_path


def transcribe_video(video_path: Path) -> str:
    """Transcribe video audio using Whisper. Returns transcript text."""
    txt_path = video_path.with_suffix('.txt')

    # Skip if already transcribed (and has content)
    if txt_path.exists() and txt_path.stat().st_size > 0:
        return txt_path.read_text()

    try:
        # Extract clean audio first
        wav_path = extract_audio(video_path)

        model = get_whisper_model()

        # Transcribe with improved settings
        segments, _ = model.transcribe(
            str(wav_path),
            language="no",
            beam_size=10,  # More thorough search
            vad_filter=True,  # Filter non-speech
            vad_parameters={"min_silence_duration_ms": 500}
        )

        # Collect all text
        text = " ".join(seg.text.strip() for seg in segments)

        # Save to file
        txt_path.write_text(text)

        # Clean up WAV file
        wav_path.unlink(missing_ok=True)

        return text
    except Exception as e:
        print(f"    Error transcribing: {e}")
        return ""


def process_clips(video_subdir: Path, video_files: list[Path], transcribe: bool = True) -> list[ClipInfo]:
    """Process clips: generate thumbnails and transcripts. Returns ClipInfo list."""
    thumb_dir = video_subdir / "thumbs"
    thumb_dir.mkdir(exist_ok=True)

    print("Processing clips...")
    clips = []

    for i, video in enumerate(video_files, 1):
        mp4_file = convert_to_mp4(video)
        print(f"  [{i}/{len(video_files)}] {mp4_file.name}")

        duration = get_video_duration(mp4_file)
        thumbs = generate_thumbnails(mp4_file, thumb_dir)

        # Load or generate transcript
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


def generate_gallery(output_dir: Path, transcribe: bool = True) -> None:
    """Generate HTML gallery from all processed videos in subdirectories."""
    print("Generating gallery...")

    # Collect all videos from subdirectories
    video_groups = []  # List of (source_name, clips)

    for subdir in sorted(output_dir.iterdir()):
        if not subdir.is_dir():
            continue
        metadata_path = subdir / "metadata.json"
        if not metadata_path.exists():
            continue

        metadata = VideoMetadata.load(metadata_path)
        video_groups.append((subdir.name, metadata.clips, subdir))
        print(f"  Found {len(metadata.clips)} clips in {subdir.name}")

    if not video_groups:
        print("  No processed videos found")
        return

    total_clips = sum(len(clips) for _, clips, _ in video_groups)
    print(f"  Total: {total_clips} clips from {len(video_groups)} videos")

    # Generate HTML
    html = '''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Video Gallery</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: #1a1a1a;
            color: #fff;
            padding: 20px;
        }
        h1 { margin-bottom: 20px; }
        .controls {
            display: flex;
            gap: 12px;
            margin-bottom: 20px;
            flex-wrap: wrap;
        }
        .search-box {
            flex: 1;
            min-width: 200px;
            padding: 10px 14px;
            font-size: 14px;
            border: 1px solid #444;
            border-radius: 6px;
            background: #2a2a2a;
            color: #fff;
        }
        .search-box:focus { outline: none; border-color: #666; }
        .btn {
            padding: 10px 16px;
            font-size: 14px;
            border: 1px solid #444;
            border-radius: 6px;
            background: #2a2a2a;
            color: #fff;
            cursor: pointer;
        }
        .btn:hover { background: #3a3a3a; }
        .source-group { margin-bottom: 24px; }
        .source-header {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px;
            background: #333;
            border-radius: 8px;
            cursor: pointer;
            margin-bottom: 12px;
        }
        .source-header:hover { background: #3a3a3a; }
        .source-header h2 {
            font-size: 16px;
            font-weight: 500;
            flex: 1;
        }
        .source-header .clip-count {
            font-size: 12px;
            color: #888;
        }
        .source-header .toggle-icon {
            font-size: 12px;
            color: #888;
            transition: transform 0.2s;
        }
        .source-group.collapsed .toggle-icon { transform: rotate(-90deg); }
        .source-group.collapsed .gallery { display: none; }
        .gallery {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(400px, 1fr));
            gap: 16px;
        }
        .video-card {
            background: #2a2a2a;
            border-radius: 8px;
            overflow: hidden;
        }
        .video-card.hidden { display: none; }
        .thumb-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 2px;
            cursor: pointer;
        }
        .thumb-grid img {
            width: 100%;
            aspect-ratio: 16/9;
            object-fit: cover;
        }
        .video-info { padding: 10px; }
        .video-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .video-name {
            font-size: 14px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        .video-duration {
            font-size: 12px;
            color: #888;
        }
        .transcript-toggle {
            font-size: 12px;
            color: #888;
            cursor: pointer;
            margin-top: 8px;
        }
        .transcript-toggle:hover { color: #aaa; }
        .transcript {
            display: none;
            margin-top: 8px;
            padding: 8px;
            background: #222;
            border-radius: 4px;
            font-size: 12px;
            line-height: 1.5;
            max-height: 200px;
            overflow-y: auto;
            white-space: pre-wrap;
        }
        .transcript.expanded { display: block; }
        .transcript mark {
            background: #665500;
            color: #fff;
        }
        .modal {
            display: none;
            position: fixed;
            top: 0; left: 0;
            width: 100%; height: 100%;
            background: rgba(0,0,0,0.9);
            z-index: 1000;
            align-items: center;
            justify-content: center;
        }
        .modal.active { display: flex; }
        .modal video {
            max-width: 90%;
            max-height: 85vh;
        }
        .modal-close {
            position: absolute;
            top: 20px;
            right: 30px;
            font-size: 40px;
            color: #fff;
            cursor: pointer;
        }
    </style>
</head>
<body>
    <h1>Video Gallery</h1>
    <div class="controls">
        <input type="text" class="search-box" id="search" placeholder="Search transcripts...">
        <button class="btn" id="expandAll">Expand All</button>
        <button class="btn" id="collapseGroups">Collapse Sources</button>
    </div>
    <div id="content">
'''

    for source_name, clips, subdir in video_groups:
        html += f'''    <div class="source-group" data-source="{source_name}">
        <div class="source-header" onclick="toggleGroup(this)">
            <span class="toggle-icon">▼</span>
            <h2>{source_name}</h2>
            <span class="clip-count">{len(clips)} clips</span>
        </div>
        <div class="gallery">
'''
        for clip in clips:
            # Prefix paths with subdir name for correct relative paths
            thumbs_html = ''.join(f'<img src="{source_name}/{t}" alt="">' for t in clip.thumbs)
            video_path = f"{source_name}/{clip.file}"
            transcript_escaped = html_lib.escape(clip.transcript)
            html += f'''            <div class="video-card" data-transcript="{transcript_escaped}" data-source="{source_name}">
                <div class="thumb-grid" onclick="playVideo('{video_path}')">{thumbs_html}</div>
                <div class="video-info">
                    <div class="video-header">
                        <div class="video-name">{clip.name}</div>
                        <div class="video-duration">{clip.duration}</div>
                    </div>
                    <div class="transcript-toggle" onclick="toggleTranscript(this)">Show transcript</div>
                    <div class="transcript">{transcript_escaped}</div>
                </div>
            </div>
'''
        html += '''        </div>
    </div>
'''

    html += '''    </div>
    <div class="modal" id="modal" onclick="closeModal(event)">
        <span class="modal-close">&times;</span>
        <video id="player" controls></video>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/fuse.js@7.0.0/dist/fuse.min.js"></script>
    <script>
        const modal = document.getElementById('modal');
        const player = document.getElementById('player');
        const search = document.getElementById('search');
        const expandBtn = document.getElementById('expandAll');
        const collapseBtn = document.getElementById('collapseGroups');
        const cards = document.querySelectorAll('.video-card');
        const groups = document.querySelectorAll('.source-group');

        // Build search index
        const cardData = Array.from(cards).map((card, i) => ({
            idx: i,
            transcript: card.dataset.transcript
        }));
        const fuse = new Fuse(cardData, {
            keys: ['transcript'],
            threshold: 0.4,
            ignoreLocation: true,
            includeMatches: true,
            minMatchCharLength: 2
        });

        function playVideo(src) {
            player.src = src;
            modal.classList.add('active');
            player.play();
        }

        function closeModal(e) {
            if (e.target === modal || e.target.classList.contains('modal-close')) {
                modal.classList.remove('active');
                player.pause();
                player.src = '';
            }
        }

        function toggleTranscript(el) {
            const transcript = el.nextElementSibling;
            const isExpanded = transcript.classList.toggle('expanded');
            el.textContent = isExpanded ? 'Hide transcript' : 'Show transcript';
        }

        function toggleGroup(header) {
            header.parentElement.classList.toggle('collapsed');
        }

        function highlightMatches(text, matches) {
            if (!matches || !matches.length) return text;
            const indices = matches[0].indices.sort((a, b) => b[0] - a[0]);
            let result = text;
            for (const [start, end] of indices) {
                result = result.slice(0, start) + '<mark>' + result.slice(start, end + 1) + '</mark>' + result.slice(end + 1);
            }
            return result;
        }

        search.addEventListener('input', () => {
            const q = search.value.trim();
            if (!q) {
                cards.forEach(card => {
                    card.classList.remove('hidden');
                    card.querySelector('.transcript').textContent = card.dataset.transcript;
                });
                groups.forEach(g => g.classList.remove('collapsed'));
                return;
            }

            const results = fuse.search(q);
            const matchedIndices = new Set(results.map(r => r.item.idx));

            cards.forEach((card, i) => {
                const isMatch = matchedIndices.has(i);
                card.classList.toggle('hidden', !isMatch);

                const transcriptEl = card.querySelector('.transcript');
                if (isMatch) {
                    const result = results.find(r => r.item.idx === i);
                    transcriptEl.innerHTML = highlightMatches(card.dataset.transcript, result.matches);
                } else {
                    transcriptEl.textContent = card.dataset.transcript;
                }
            });

            // Expand groups with matches
            groups.forEach(g => g.classList.remove('collapsed'));
        });

        let allExpanded = false;
        expandBtn.addEventListener('click', () => {
            allExpanded = !allExpanded;
            expandBtn.textContent = allExpanded ? 'Collapse All' : 'Expand All';
            cards.forEach(card => {
                const transcript = card.querySelector('.transcript');
                const toggle = card.querySelector('.transcript-toggle');
                if (allExpanded) {
                    transcript.classList.add('expanded');
                    toggle.textContent = 'Hide transcript';
                } else {
                    transcript.classList.remove('expanded');
                    toggle.textContent = 'Show transcript';
                }
            });
        });

        let groupsCollapsed = false;
        collapseBtn.addEventListener('click', () => {
            groupsCollapsed = !groupsCollapsed;
            collapseBtn.textContent = groupsCollapsed ? 'Expand Sources' : 'Collapse Sources';
            groups.forEach(g => g.classList.toggle('collapsed', groupsCollapsed));
        });

        document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal({target: modal}); });
    </script>
</body>
</html>
'''

    gallery_path = output_dir / "gallery.html"
    gallery_path.write_text(html)
    print(f"  Gallery: {gallery_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Split video at recording boundaries using multi-signal detection"
    )
    parser.add_argument("input", type=Path, nargs="?", help="Input video file")
    parser.add_argument("--output-dir", type=Path, required=True, help="Output directory")
    parser.add_argument("--min-confidence", type=int, default=45,
                        help="Minimum confidence score for cuts (default: 45)")
    parser.add_argument("--min-gap", type=float, default=10.0,
                        help="Minimum gap between cuts in seconds (default: 10)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show detected cuts without splitting")
    parser.add_argument("--gallery-only", action="store_true",
                        help="Only regenerate gallery from existing files in output-dir")
    parser.add_argument("--skip-transcribe", action="store_true",
                        help="Skip transcription step")
    parser.add_argument("--transcribe-only", action="store_true",
                        help="Only run transcription on existing videos in output-dir")
    parser.add_argument("--force", action="store_true",
                        help="Force reprocessing even if already processed")

    args = parser.parse_args()

    # Helper to find video subdirectories
    def find_video_subdirs():
        if not args.output_dir.exists():
            print(f"Error: Output directory not found: {args.output_dir}", file=sys.stderr)
            sys.exit(1)

        subdirs = []
        for subdir in sorted(args.output_dir.iterdir()):
            if subdir.is_dir():
                # Check for video files in subdir
                extensions = {'.avi', '.mp4', '.mov', '.mkv', '.webm', '.wmv', '.flv'}
                videos = [f for f in subdir.iterdir()
                          if f.is_file() and f.suffix.lower() in extensions]
                if videos:
                    subdirs.append((subdir, videos))
        return subdirs

    # Transcribe-only mode
    if args.transcribe_only:
        subdirs = find_video_subdirs()
        if not subdirs:
            print("No video subdirectories found")
            sys.exit(1)

        total = sum(len(videos) for _, videos in subdirs)
        print(f"Transcribing {total} videos in {len(subdirs)} subdirectories")

        for subdir, videos in subdirs:
            print(f"\n{subdir.name}:")
            for video in sorted(videos):
                mp4_file = convert_to_mp4(video)
                print(f"  {mp4_file.name}")
                transcribe_video(mp4_file)

            # Update metadata if exists
            metadata_path = subdir / "metadata.json"
            if metadata_path.exists():
                metadata = VideoMetadata.load(metadata_path)
                for clip in metadata.clips:
                    txt_path = subdir / clip.file.replace('.mp4', '.txt')
                    if txt_path.exists():
                        clip.transcript = txt_path.read_text()
                metadata.save(metadata_path)

        print("\nDone!")
        return

    # Gallery-only mode: regenerate metadata and gallery from existing files
    if args.gallery_only:
        subdirs = find_video_subdirs()
        if not subdirs:
            print("No video subdirectories found")
            sys.exit(1)

        print(f"Processing {len(subdirs)} video subdirectories...")
        for subdir, videos in subdirs:
            metadata_path = subdir / "metadata.json"

            # Generate/update metadata for each subdir
            print(f"\n{subdir.name}:")
            clips = process_clips(subdir, sorted(videos), transcribe=not args.skip_transcribe)
            metadata = VideoMetadata(
                source_file=subdir.name,
                processed_date=datetime.now().isoformat(),
                clips=clips
            )
            metadata.save(metadata_path)
            update_catalog(args.output_dir, subdir.name, subdir.name, len(clips))

        generate_gallery(args.output_dir, transcribe=not args.skip_transcribe)
        print("\nDone!")
        return

    if not args.input:
        print("Error: input file required (unless using --gallery-only)", file=sys.stderr)
        sys.exit(1)

    if not args.input.exists():
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    # Create subdirectory for this source video
    video_name = args.input.stem
    video_subdir = args.output_dir / video_name
    metadata_path = video_subdir / "metadata.json"

    # Check if already processed
    if metadata_path.exists() and not args.force:
        print(f"Already processed: {video_name} (use --force to reprocess)")
        print("Regenerating gallery...")
        generate_gallery(args.output_dir, transcribe=not args.skip_transcribe)
        return

    # Remove existing output if forcing
    if video_subdir.exists() and args.force:
        print(f"Removing existing output: {video_subdir}")
        shutil.rmtree(video_subdir)

    print(f"Analyzing: {args.input}")
    print()

    # Get video duration
    duration = get_video_duration(args.input)
    print(f"Duration: {format_time(duration)}")
    print()

    # Multi-signal detection
    print("Running detection...")
    scenes = detect_scenes(args.input)
    blacks = detect_black_frames(args.input)
    audio_changes = detect_audio_changes(args.input, duration)

    print(f"  Found {len(scenes)} scene changes, {len(blacks)} black frames, {len(audio_changes)} audio changes")
    print()

    # Find cuts
    print(f"Finding cuts (min_confidence={args.min_confidence}, min_gap={args.min_gap}s)...")
    cuts = find_cuts(scenes, blacks, audio_changes, args.min_confidence, args.min_gap)

    if not cuts:
        print("No cuts detected. Try lowering --min-confidence")
        sys.exit(0)

    print(f"\nFound {len(cuts)} cut(s):")
    for cut in cuts:
        print(f"  {format_time(cut.time)} [score:{cut.confidence_score:3d}] ({cut.signal_summary()})")
    print()

    num_segments = len(cuts) + 1
    print(f"Will create {num_segments} segment(s)")
    print()

    if args.dry_run:
        print("[Dry run - no files created]")
        return

    print(f"Splitting to: {video_subdir}")
    output_files = split_video(args.input, video_subdir, cuts, duration)
    print()

    # Process clips and save metadata
    clips = process_clips(video_subdir, output_files, transcribe=not args.skip_transcribe)
    metadata = VideoMetadata(
        source_file=args.input.name,
        processed_date=datetime.now().isoformat(),
        clips=clips
    )
    metadata.save(metadata_path)

    # Update catalog
    update_catalog(args.output_dir, video_name, args.input.name, len(clips))

    # Generate combined gallery
    generate_gallery(args.output_dir, transcribe=not args.skip_transcribe)
    print()
    print("Done!")


if __name__ == "__main__":
    main()
