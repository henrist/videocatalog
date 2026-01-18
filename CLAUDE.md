# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                                    # Install dependencies
uv run videocatalog process video.avi      # Process video (outputs to ./output)
uv run videocatalog serve                  # Start edit server
```

**Docs:** CLI options documented in README.md - update when changing arguments.

## After Code Changes

Always run before committing:

```bash
uv run ruff format . && uv run ruff check . && uv run ty check
```

## Architecture

Video processing pipeline that splits recordings at detected boundaries and generates a searchable web gallery.

**Core flow:** `cli.py` → `detection.py` → `splitting.py` → `transcription.py` → `gallery.py`

- **cli.py**: Subcommand parsing (process, serve, preprocess, transcribe, gallery)
- **detection.py**: Scene/black/audio detection, verification, find_cuts, detect_cuts
- **splitting.py**: Split video at detected cut boundaries
- **thumbnails.py**: Thumbnail and sprite generation
- **transcription.py**: Whisper transcription
- **preprocess.py**: DV file preprocessing (deinterlace, convert to MP4)
- **processing.py**: process_clips orchestration and convert_to_mp4
- **utils.py**: Shared utilities (run_ffmpeg, get_video_duration, format_*)
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
└── video_name/
    ├── metadata.json      # ClipInfo list
    ├── user_edits.json    # Tags, year, descriptions
    ├── thumbs/
    └── *.mp4, *.txt
```

**Parallelization:** Uses ThreadPoolExecutor for FFmpeg operations, multiprocessing with spawn context for Whisper (each worker loads own model, ~3GB RAM each).
