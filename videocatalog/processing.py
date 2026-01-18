"""Video processing orchestration: process_clips and convert_to_mp4."""

import multiprocessing
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .models import ClipInfo
from .thumbnails import create_sprite, generate_thumbnails
from .transcription import extract_audio, transcribe_from_wav, transcribe_worker
from .utils import (
    format_duration,
    get_default_workers,
    get_video_duration,
    has_content,
    run_ffmpeg,
)


def convert_to_mp4(video_path: Path, threads: int = 0, log: Callable[[str], None] = print) -> Path:
    """Convert video to MP4 if not already.

    Args:
        threads: Number of encoding threads (0 = auto/all cores)
    """
    if video_path.suffix.lower() == ".mp4":
        return video_path

    mp4_path = video_path.with_suffix(".mp4")
    if mp4_path.exists():
        return mp4_path

    log("    Converting to MP4...")
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        "yadif,hqdn3d",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "22",
        "-threads",
        str(threads),
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(mp4_path),
    ]
    run_ffmpeg(cmd, check=True)
    return mp4_path


def process_clips(
    video_subdir: Path,
    video_files: list[Path],
    transcribe: bool = True,
    workers: int = 0,
    transcribe_workers: int = 1,
    log: Callable[[str], None] = print,
) -> list[ClipInfo]:
    """Process clips with parallel audio extraction and thumbnails."""
    thumb_dir = video_subdir / "thumbs"
    thumb_dir.mkdir(exist_ok=True)

    if workers <= 0:
        workers = get_default_workers()

    log(f"Processing {len(video_files)} clips (workers={workers})...")

    # Phase 1: Convert to MP4 if needed (parallel)
    non_mp4 = [(i, v) for i, v in enumerate(video_files) if v.suffix.lower() != ".mp4"]
    mp4_results = {}  # index -> mp4_path
    if non_mp4:
        # Limit parallelism: fewer workers than files = more threads per worker
        cpu_count = os.cpu_count() or 4
        convert_workers = min(workers, len(non_mp4))
        threads = max(1, cpu_count // convert_workers)
        log(
            f"  Converting {len(non_mp4)} non-MP4 files ({convert_workers} workers, {threads} threads each)..."
        )
        with ThreadPoolExecutor(max_workers=convert_workers) as executor:
            futures = {executor.submit(convert_to_mp4, v, threads, log): i for i, v in non_mp4}
            for future in as_completed(futures):
                idx = futures[future]
                try:
                    mp4_results[idx] = future.result()
                except Exception as e:
                    log(f"    Error converting {video_files[idx].name}: {e}")
                    mp4_results[idx] = video_files[idx]  # keep original on error
    # Build mp4_files list preserving order
    mp4_files = [
        mp4_results.get(i, v if v.suffix.lower() == ".mp4" else v.with_suffix(".mp4"))
        for i, v in enumerate(video_files)
    ]

    # Phase 2: Get durations for all files (needed for thumbnails and final clip info)
    log("  Getting durations...")
    durations = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(get_video_duration, f): f for f in mp4_files}
        for future in as_completed(futures):
            mp4 = futures[future]
            try:
                durations[mp4] = future.result()
            except Exception as e:
                log(f"    Error getting duration for {mp4.name}: {e}")
                durations[mp4] = 0.0

    # Phase 3: Extract audio in parallel (for files needing transcription)
    wav_map = {}  # mp4_path -> wav_path
    if transcribe:
        to_transcribe = [f for f in mp4_files if not f.with_suffix(".txt").exists()]
        if to_transcribe:
            log(f"  Extracting audio for {len(to_transcribe)} files...")
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(extract_audio, f): f for f in to_transcribe}
                for future in as_completed(futures):
                    mp4 = futures[future]
                    try:
                        wav_map[mp4] = future.result()
                    except Exception as e:
                        log(f"    Error extracting audio for {mp4.name}: {e}")

    # Phase 4: Transcribe (parallel with multiprocessing if transcribe_workers > 1)
    transcripts = {}
    if transcribe and wav_map:
        log(f"  Transcribing {len(wav_map)} files ({transcribe_workers} workers)...")
        try:
            if transcribe_workers == 1:
                # Sequential - no multiprocessing overhead
                for i, (mp4, wav) in enumerate(wav_map.items(), 1):
                    log(f"    [{i}/{len(wav_map)}] {mp4.name}")
                    transcripts[mp4] = transcribe_from_wav(mp4, wav)
            else:
                # Parallel - each process loads own model
                work_items = [(str(mp4), str(wav)) for mp4, wav in wav_map.items()]
                ctx = multiprocessing.get_context("spawn")
                pool = ctx.Pool(processes=transcribe_workers)
                try:
                    for i, (video_path_str, transcript) in enumerate(
                        pool.imap_unordered(transcribe_worker, work_items), 1
                    ):
                        log(f"    [{i}/{len(wav_map)}] {Path(video_path_str).name}")
                        transcripts[Path(video_path_str)] = transcript
                finally:
                    pool.close()
                    pool.join()
        except Exception:
            # Clean up WAV files on error
            for wav in wav_map.values():
                wav.unlink(missing_ok=True)
            raise

    # Phase 5: Generate thumbnails and sprites in parallel
    log("  Generating thumbnails...")
    thumb_results = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(generate_thumbnails, f, thumb_dir, durations[f]): f for f in mp4_files
        }
        for future in as_completed(futures):
            mp4 = futures[future]
            try:
                thumb_results[mp4] = future.result()
            except Exception as e:
                log(f"    Error generating thumbnails for {mp4.name}: {e}")
                thumb_results[mp4] = []

    # Create sprites from thumbnails (deletes individual thumbs)
    log("  Creating sprites...")
    sprite_results = {}
    for mp4_file in mp4_files:
        thumbs = thumb_results.get(mp4_file, [])
        sprite_results[mp4_file] = create_sprite(thumb_dir, thumbs, mp4_file.stem)

    # Build final clip list (preserve original order)
    clips = []
    for mp4_file in mp4_files:
        txt_path = mp4_file.with_suffix(".txt")
        if mp4_file in transcripts:
            transcript = transcripts[mp4_file]
        elif has_content(txt_path):
            transcript = txt_path.read_text()
        else:
            transcript = ""

        duration = durations.get(mp4_file, 0.0)
        sprite = sprite_results.get(mp4_file)

        clips.append(
            ClipInfo(
                file=mp4_file.name,
                name=mp4_file.stem,
                thumbs=[],
                sprite=f"thumbs/{sprite}" if sprite else None,
                duration=format_duration(duration),
                transcript=transcript,
            )
        )

    return clips
