# videocatalog

Turn hours of old video camera footage into a searchable gallery.

Automatically detects recording boundaries, splits into clips, transcribes audio, and generates a web page where you can browse thumbnails, search transcripts, and play clips.

## Usage

```bash
# Process a video file
make split INPUT=video.avi

# Regenerate gallery from existing clips
make gallery
```

Output goes to `output/` with a `gallery.html` you can open in any browser.

## Requirements

- Docker, or
- Python 3.12+ with ffmpeg installed (`uv sync` to install deps)
