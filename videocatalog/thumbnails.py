"""Thumbnail and sprite generation for video clips."""

import subprocess
from pathlib import Path

from PIL import Image, ImageOps

SPRITE_THUMB_W, SPRITE_THUMB_H = 320, 180
SPRITE_GAP = 2


def generate_thumbnails(
    video_path: Path, thumb_dir: Path, duration: float, count: int = 12
) -> list[str]:
    """Generate multiple thumbnails from video, always including first and last frame."""
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
            "ffmpeg",
            "-y",
            "-ss",
            str(seek),
            "-i",
            str(video_path),
            "-vframes",
            "1",
            "-q:v",
            "3",
            str(thumb_path),
        ]
        subprocess.run(cmd, capture_output=True)
        thumbs.append(thumb_name)

    return thumbs


def create_sprite(thumb_dir: Path, thumb_names: list[str], video_stem: str) -> str | None:
    """Create sprite from thumbnails and delete originals. Returns sprite filename."""
    if not thumb_names:
        return None

    cols, rows = 4, 3
    sprite_w = SPRITE_THUMB_W * cols + SPRITE_GAP * (cols - 1)
    sprite_h = SPRITE_THUMB_H * rows + SPRITE_GAP * (rows - 1)
    sprite = Image.new("RGBA", (sprite_w, sprite_h), (0, 0, 0, 0))

    for i, thumb_name in enumerate(thumb_names):
        thumb_path = thumb_dir / thumb_name
        if not thumb_path.exists():
            continue
        col, row = i % cols, i // cols
        x = col * (SPRITE_THUMB_W + SPRITE_GAP)
        y = row * (SPRITE_THUMB_H + SPRITE_GAP)
        img = ImageOps.fit(
            Image.open(thumb_path), (SPRITE_THUMB_W, SPRITE_THUMB_H), Image.Resampling.LANCZOS
        )
        sprite.paste(img, (x, y))
        thumb_path.unlink()

    sprite_name = f"{video_stem}_sprite.webp"
    sprite.save(thumb_dir / sprite_name, "WEBP", quality=85)
    return sprite_name
