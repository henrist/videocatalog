"""Video preprocessing: convert DV and film scan files to efficient MP4."""

from pathlib import Path

from .utils import run_ffmpeg


def preprocess_dv_file(input_path: Path, output_path: Path, threads: int = 0) -> None:
    """Convert DV file to MP4 with deinterlacing.

    Designed for DV25 PAL source (~30 Mbps, 576i interlaced).
    Output: H.264 MP4, CRF 18, deinterlaced.

    Args:
        threads: Number of encoding threads (0 = auto/all cores)
    """
    temp_path = output_path.with_suffix(".tmp.mp4")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        "yadif",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-threads",
        str(threads),
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(temp_path),
    ]
    run_ffmpeg(cmd, check=True)
    temp_path.rename(output_path)


def preprocess_film_scan(input_path: Path, output_path: Path, threads: int = 0) -> None:
    """Re-encode high-bitrate film scan MP4 to efficient H.264.

    Designed for 8mm/Super 8 film scans. Preserves frame rate and resolution.
    No deinterlacing (source is progressive).

    Args:
        threads: Number of encoding threads (0 = auto/all cores)
    """
    temp_path = output_path.with_suffix(".tmp.mp4")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-threads",
        str(threads),
        "-c:a",
        "copy",
        str(temp_path),
    ]
    run_ffmpeg(cmd, check=True)
    temp_path.rename(output_path)
