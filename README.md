# videocatalog

Turn hours of old video camera footage into a searchable gallery.

Automatically detects recording boundaries, splits into clips, transcribes audio, and generates a web page where you can browse thumbnails, search transcripts, and play clips.

## Requirements

- Python 3.12+ with ffmpeg installed
- [uv](https://docs.astral.sh/uv/) for dependency management

## Usage

```bash
# Install dependencies
uv sync

# Preprocess DV files (deinterlace, convert to MP4)
uv run videocatalog --preprocess /path/to/dv --target-dir /path/to/mp4

# Process a video file
uv run videocatalog video.avi --output-dir output

# View gallery (read-only)
open output/gallery.html

# View and edit (start server, then open browser)
uv run videocatalog --output-dir output --serve --regenerate
open http://localhost:8000
```

### Options

- `--min-confidence N` - Minimum score for cut detection (default: 45)
- `--min-gap N` - Minimum seconds between cuts (default: 10)
- `--dry-run` - Show detected cuts without splitting
- `--skip-transcribe` - Skip whisper transcription
- `--transcribe-only` - Only transcribe existing clips
- `--gallery-only` - Reprocess clips and regenerate gallery
- `--html-only` - Only regenerate gallery HTML (fast)
- `--serve` - Start web server for editing tags/year
- `--regenerate` - Regenerate gallery HTML on each page load
- `--host` / `--port` - Server bind options
- `--workers N` - Parallel workers for ffmpeg operations (default: auto)
- `--transcribe-workers N` - Parallel Whisper instances (default: 1, each uses ~3GB RAM)
- `--preprocess` - Convert DV files to MP4 with deinterlacing (use with `--target-dir`)
- `--target-dir PATH` - Target directory for preprocessed files

## Docker

```bash
./scripts/docker-build.sh
./scripts/docker-run.sh --mount /path/to/videos -- /path/to/videos/video.avi
./scripts/docker-run.sh -- --gallery-only
./scripts/docker-serve.sh
```

## Testing

Regression tests validate the scene detection algorithm against known videos.

```bash
# Install dev dependencies
uv sync --extra dev

# Generate golden file from first 3 minutes of video (times in seconds)
uv run python -m tests.generate_golden recording.avi --start 0 --end 180

# Run tests
uv run pytest tests/ -v

# Quick check: only analyze first 2 minutes
VIDEOCATALOG_TEST_LIMIT=120 uv run pytest tests/ -v

# Use different video directory
VIDEOCATALOG_TEST_VIDEOS=/path/to/videos uv run pytest tests/ -v
```

Golden files in `tests/golden/` store expected cut times. Regenerate after intentional algorithm changes.
