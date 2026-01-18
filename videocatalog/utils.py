"""Shared utilities for video processing."""

import os
import re
import subprocess
from pathlib import Path


class SubprocessError(Exception):
    """Raised when a subprocess command fails."""

    pass


def run_ffmpeg(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    """Run an ffmpeg/ffprobe command, optionally checking for errors."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SubprocessError(f"Command failed: {' '.join(cmd[:3])}...\n{result.stderr[:500]}")
    return result


def has_content(path: Path) -> bool:
    """Check if file exists and has content."""
    try:
        return path.stat().st_size > 0
    except FileNotFoundError:
        return False


def get_video_duration(video_path: Path) -> float:
    """Get video duration in seconds."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    result = run_ffmpeg(cmd, check=True)
    try:
        return float(result.stdout.strip())
    except ValueError as e:
        raise SubprocessError(f"Invalid duration from ffprobe: {result.stdout!r}") from e


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


def get_default_workers() -> int:
    """Get default worker count for ffmpeg operations."""
    return min(os.cpu_count() or 4, 8)


def parse_timestamp(value: str) -> float:
    """Parse timestamp string to seconds.

    Accepts:
      - Seconds as float: "47.5" → 47.5
      - Timestamp format: "47m40s" → 2860.0, "1h2m3s" → 3723.0
    """
    # Try parsing as plain float first
    try:
        return float(value)
    except ValueError:
        pass

    # Parse timestamp format: 1h2m3s, 2m30s, 45s, etc.
    pattern = r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s?)?$"
    match = re.match(pattern, value)
    if not match:
        raise ValueError(f"Invalid timestamp format: {value}")

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    seconds = float(match.group(3) or 0)

    return hours * 3600 + minutes * 60 + seconds


def get_video_fps(video_path: Path) -> float:
    """Get video frame rate."""
    cmd = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=r_frame_rate",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(video_path),
    ]
    result = run_ffmpeg(cmd, check=True)
    # r_frame_rate returns "30/1" or "30000/1001" format
    fps_str = result.stdout.strip()
    if "/" in fps_str:
        num, den = fps_str.split("/")
        return float(num) / float(den)
    return float(fps_str)
