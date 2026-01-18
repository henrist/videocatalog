"""DV file preprocessing: convert to MP4 with deinterlacing."""

from pathlib import Path

from .utils import run_ffmpeg


def preprocess_dv_file(input_path: Path, output_path: Path) -> None:
    """Convert DV file to MP4 with deinterlacing.

    Designed for DV25 PAL source (~30 Mbps, 576i interlaced).
    Output: H.264 MP4, CRF 18, deinterlaced.
    """
    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-vf", "yadif",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        str(output_path)
    ]
    run_ffmpeg(cmd, check=True)
