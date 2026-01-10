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

# Process a video file
uv run videocatalog video.avi --output-dir output

# View gallery (read-only)
open output/gallery.html

# View and edit (start server, then open browser)
uv run videocatalog --output-dir output --serve
open http://localhost:8000
```

### Options

- `--min-confidence N` - Minimum score for cut detection (default: 45)
- `--min-gap N` - Minimum seconds between cuts (default: 10)
- `--dry-run` - Show detected cuts without splitting
- `--skip-transcribe` - Skip whisper transcription
- `--transcribe-only` - Only transcribe existing clips
- `--gallery-only` - Only regenerate gallery HTML
- `--serve` - Start web server for editing tags/year
- `--host` / `--port` - Server bind options

## Docker

Alternatively, use Docker via Makefile:

```bash
make build
make run INPUT=video.avi
make run ARGS='--gallery-only'
make serve
```
