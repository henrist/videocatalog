"""Video splitting at detected cut boundaries."""

from collections.abc import Callable
from pathlib import Path

from .models import CutCandidate
from .utils import format_time, format_time_filename, run_ffmpeg


def split_video(
    video_path: Path,
    output_dir: Path,
    cuts: list[CutCandidate],
    duration: float,
    log: Callable[[str], None] = print,
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

        log(
            f"  Segment {segment_num}: {format_time(start)} -> {format_time(end)} => {output_path.name}"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start),
            "-i",
            str(video_path),
            "-t",
            str(end - start),
            "-vf",
            "yadif,hqdn3d",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "22",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            str(output_path),
        ]

        run_ffmpeg(cmd, check=True)

    return output_files
