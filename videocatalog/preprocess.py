"""DV file preprocessing: convert to MP4 with deinterlacing."""

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
