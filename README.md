# videocatalog

Turn hours of old video camera footage into a searchable gallery.

Automatically detects recording boundaries, splits into clips, transcribes audio, and generates a web page where you can browse thumbnails, search transcripts, and play clips.

## Preprocessing

The `preprocess` command converts source files to efficient H.264 MP4 for use with the main `process` command.

### Interlaced video (`--type dv`)

Applies yadif deinterlacing → H.264 CRF 18. Use for interlaced sources like DV captures.

### Progressive video (`--type film-scan`)

Re-encodes to H.264 CRF 18 without deinterlacing. Preserves original frame rate and resolution. Use for high-bitrate progressive sources like film scans.

## Tested Source Material

This tool was developed and tested with the following source material:

### DV captures

DV25-encoded AVI files (720×576 PAL, 25fps, ~29 Mbps, interlaced) digitized from VHS, Hi8, and MiniDV tapes. These analog/digital formats have different effective resolutions (~240-500 lines) but produce identical DV25 files when captured.

### Film scans

High-bitrate H.264 MP4 files (2540×1530, 50-75 Mbps, progressive) from 8mm (16 fps) and Super 8 (18 fps) film scanners.

## Requirements

- Python 3.12+ with ffmpeg installed
- [uv](https://docs.astral.sh/uv/) for dependency management

## Usage

```bash
# Install dependencies
uv sync

# Show available commands or help
uv run videocatalog -h

# Process a video file (full pipeline, outputs to ./output)
uv run videocatalog process video.avi

# Preprocess DV files (deinterlace, convert to MP4)
uv run videocatalog preprocess /path/to/dv --target-dir /path/to/mp4

# Transcribe existing clips
uv run videocatalog transcribe

# Regenerate gallery only
uv run videocatalog gallery

# View gallery (read-only)
open output/gallery.html

# View and edit (start server, then open browser)
uv run videocatalog serve --regenerate
open http://localhost:8000
```

### Subcommands

**`process INPUT`** - Full pipeline: detect → split → transcribe → gallery
- `--output-dir` - Output directory (default: output)
- `--name` - Override output subdirectory name
- `--min-confidence N` - Minimum score for cut detection (default: 12)
- `--min-gap N` - Minimum seconds between cuts (default: 1)
- `--start N` / `--limit N` - Process subset of video
- `--dry-run` - Show detected cuts without splitting
- `--verbose` / `-v` - Show detailed detection info
- `--force` - Reprocess even if already processed
- `--skip-transcribe` - Skip whisper transcription
- `--workers N` - Parallel workers for ffmpeg (default: auto)
- `--transcribe-workers N` - Parallel Whisper instances (default: 1, ~3GB RAM each)

**`serve`** - Start web server for viewing and editing
- `--output-dir` - Output directory (default: output)
- `--host` / `--port` - Server bind options (default: 127.0.0.1:8000)
- `--regenerate` - Regenerate gallery HTML on each page load

**`preprocess INPUT --target-dir DIR`** - Convert DV/film scan files to MP4
- `--workers N` - Parallel workers (default: auto)
- `--type dv|film-scan` - Source type (default: auto-detect from extension)

**`transcribe`** - Transcribe existing clips
- `--output-dir` - Output directory (default: output)
- `--workers N` - Parallel workers for ffmpeg (default: auto)
- `--transcribe-workers N` - Parallel Whisper instances (default: 1)

**`gallery`** - Regenerate gallery.html only
- `--output-dir` - Output directory (default: output)

## Docker

```bash
./scripts/docker-build.sh
./scripts/docker-run.sh --mount /path/to/videos -- process /path/to/videos/video.avi
./scripts/docker-run.sh -- gallery
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
