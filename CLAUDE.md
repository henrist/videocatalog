# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                                    # Install dependencies
uv run videocatalog video.avi --output-dir out  # Process video
uv run videocatalog --output-dir out --serve    # Start edit server
uv run python -c "from videocatalog import cli, server, processing, gallery"  # Verify imports
```

**Docs:** CLI options documented in README.md - update when changing arguments.

## Architecture

Video processing pipeline that splits recordings at detected boundaries and generates a searchable web gallery.

**Core flow:** `cli.py` → `processing.py` → `gallery.py`

- **cli.py**: Entry point, argument parsing, orchestrates processing modes
- **processing.py**: FFmpeg operations (scene detection, black frames, audio analysis, splitting, transcription via faster-whisper)
- **gallery.py**: Generates single-file HTML gallery with embedded JavaScript for search/filtering
- **server.py**: FastAPI server for editing tags/year metadata, serves static video files
- **models.py**: Pydantic models for all data structures (clips, metadata, user edits, cut candidates)

**Data flow:**
1. Detection phase: FFmpeg analyzes video for scene changes, black frames, audio level changes
2. `CutCandidate` scoring combines signals to find recording boundaries
3. Split phase: FFmpeg segments video at cut points, converts to MP4
4. Transcription: faster-whisper processes audio (Norwegian language hardcoded)
5. Gallery generation: Creates HTML with thumbnails, transcripts, Fuse.js search

**Output structure:**
```
output_dir/
├── gallery.html
├── catalog.json
└── video_name/
    ├── metadata.json      # ClipInfo list
    ├── user_edits.json    # Tags, year, descriptions
    ├── thumbs/
    └── *.mp4, *.txt
```

**Parallelization:** Uses ThreadPoolExecutor for FFmpeg operations, multiprocessing with spawn context for Whisper (each worker loads own model, ~3GB RAM each).
