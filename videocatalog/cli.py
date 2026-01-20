"""Command-line interface for videocatalog."""

import argparse
import io
import multiprocessing
import os
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from .detection import detect_cuts
from .gallery import generate_gallery
from .models import (
    AudioChange,
    BlackDetection,
    CandidateInfo,
    DetectionData,
    SceneDetection,
    SegmentInfo,
    SplitParameters,
    SplitsFile,
    VideoMetadata,
)
from .preprocess import preprocess_dv_file, preprocess_film_scan
from .processing import convert_to_mp4, process_clips
from .splitting import split_video
from .transcription import extract_audio, transcribe_from_wav, transcribe_worker
from .utils import (
    format_time,
    get_default_workers,
    get_video_duration,
    get_video_fps,
    parse_timestamp,
    run_ffmpeg,
)


@contextmanager
def detection_log_file(log_path: Path | None):
    """Context manager that yields a file or StringIO if log_path is None."""
    if log_path is None:
        yield io.StringIO()
    else:
        with open(log_path, "w") as f:
            yield f


def find_video_subdirs(output_dir: Path) -> list[tuple[Path, list[Path]]]:
    """Find subdirectories containing video files."""
    if not output_dir.exists():
        print(f"Error: Output directory not found: {output_dir}", file=sys.stderr)
        sys.exit(1)

    subdirs = []
    for subdir in sorted(output_dir.iterdir()):
        if subdir.is_dir():
            extensions = {".avi", ".mp4", ".mov", ".mkv", ".webm", ".wmv", ".flv"}
            videos = [f for f in subdir.iterdir() if f.is_file() and f.suffix.lower() in extensions]
            if videos:
                subdirs.append((subdir, videos))
    return subdirs


def run_detection_with_logging(
    video_path: Path,
    log_file,
    start_time: float,
    end_time: float,
    min_confidence: int,
    min_gap: float,
    verbose: bool,
):
    """Run cut detection and write detailed logs. Returns (result, cuts, all_candidates)."""

    def log(msg: str):
        """Write to log file always, console only if verbose."""
        log_file.write(msg + "\n")
        if verbose:
            print(msg)

    def log_always(msg: str):
        """Write to both log file and console."""
        log_file.write(msg + "\n")
        print(msg)

    result = detect_cuts(
        video_path,
        start_time=start_time,
        end_time=end_time,
        min_confidence=min_confidence,
        min_gap=min_gap,
        verbose=verbose,
        log_file=log_file,
    )
    cuts = result.cuts
    all_candidates = result.all_candidates

    # Summary
    noise_info = f", {len(result.noise_zones)} noise zones" if result.noise_zones else ""
    log_always(
        f"  Found {len(result.scenes)} scene changes, {len(result.blacks)} black frames, {len(result.audio_changes)} audio changes{noise_info}"
    )
    log_always(f"  Analyzed {len(all_candidates)} candidates, verified {len(cuts)} cuts")

    # Raw detection data (verbose only)
    log("")
    log("=== RAW DETECTION DATA ===")
    log("")
    log("Scene changes (time, score) - threshold >=5:")
    for time, score in sorted(result.scenes):
        marker = " ***" if score >= 15 else " **" if score >= 10 else " *" if score >= 6 else ""
        log(f"  {format_time(time)} score={score:5.1f}{marker}")
    log("")
    log("Black frames (end_time, duration) - all >=0.1s:")
    for black_end, dur in sorted(result.blacks):
        marker = " ***" if dur >= 1.0 else " **" if dur >= 0.5 else " *" if dur >= 0.2 else ""
        log(f"  {format_time(black_end)} dur={dur:.2f}s{marker}")
    log("")
    log("Audio level jumps (time, step_dB) - threshold >10dB:")
    for t in sorted(result.audio_changes.keys()):
        step = result.audio_changes[t]
        marker = " ***" if step >= 25 else " **" if step >= 18 else " *" if step >= 12 else ""
        log(f"  {format_time(t)} step={step:5.1f}dB{marker}")
    log("")
    log("Legend: * = low score, ** = medium, *** = high")
    if result.noise_zones:
        log("")
        log("Noise zones (suppressed interior detections):")
        for zone in result.noise_zones:
            log(
                f"  {format_time(zone.start)} - {format_time(zone.end)} ({zone.end - zone.start:.1f}s, {zone.detection_count} detections)"
            )

    # All candidates (verbose only)
    log("")
    log("=== ALL CANDIDATES (chronological) ===")
    log("Scoring: scene(0-40) + black(0-35) + audio(0-30) = max 105")
    log("")
    for c in sorted(all_candidates, key=lambda x: x.time):
        selected = (
            "SELECTED"
            if c in cuts
            else f"skip (below {min_confidence})"
            if c.confidence_score < min_confidence
            else "skip (too close)"
        )
        s, b, a = c.score_breakdown()
        score_str = f"[{c.confidence_score:3d}={s:2d}+{b:2d}+{a:2d}]"
        log(f"  {format_time(c.time)} {score_str} {c.signal_summary():40s} -> {selected}")
    log("")

    # Final cuts (always)
    if cuts:
        log_always(f"\nFound {len(cuts)} cut(s):")
        for cut in cuts:
            log_always(
                f"  {format_time(cut.time)} [score:{cut.confidence_score:3d}] ({cut.signal_summary()})"
            )
        log_always("")
        log_always(f"Will create {len(cuts) + 1} segment(s)")
        log_always("")
    else:
        log_always("No cuts detected. Try lowering --min-confidence")

    return result, cuts, all_candidates


def cmd_process(args):
    """Full pipeline: detect → split → transcribe → gallery."""
    if not args.input.exists():
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    video_name = args.name if args.name else args.input.stem
    video_subdir = args.output_dir / video_name
    metadata_path = video_subdir / "metadata.json"

    if metadata_path.exists() and not args.force:
        print(f"Already processed: {video_name} (use --force to reprocess)")
        print("Regenerating gallery...")
        generate_gallery(args.output_dir, transcribe=not args.skip_transcribe)
        return

    if video_subdir.exists() and args.force:
        print(f"Removing existing output (keeping transcripts): {video_subdir}")
        for f in video_subdir.glob("*.mp4"):
            f.unlink()
        for f in video_subdir.glob("*.wav"):
            f.unlink()
        thumbs_dir = video_subdir / "thumbs"
        if thumbs_dir.exists():
            shutil.rmtree(thumbs_dir)
        if metadata_path.exists():
            metadata_path.unlink()

    # Create log file for detection output
    log_path = None
    if not args.dry_run:
        video_subdir.mkdir(parents=True, exist_ok=True)
        log_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = video_subdir / f"detection_log_{log_timestamp}.txt"

    with detection_log_file(log_path) as log_file:

        def log(msg: str):
            """Write to both console and log file."""
            print(msg)
            log_file.write(msg + "\n")

        log(f"Analyzing: {args.input}")
        log("")

        # Calculate time range
        full_duration = get_video_duration(args.input)
        start_time = args.start
        end_time = (start_time + args.limit) if args.limit > 0 else full_duration
        end_time = min(end_time, full_duration)

        if start_time > 0 or end_time < full_duration:
            log(
                f"Duration: {format_time(full_duration)} (analyzing {format_time(start_time)} - {format_time(end_time)})"
            )
        else:
            log(f"Duration: {format_time(full_duration)}")
        log(f"min_confidence={args.min_confidence}, min_gap={args.min_gap}")
        log("")

        log("Running detection...")

        result, cuts, all_candidates = run_detection_with_logging(
            args.input,
            log_file,
            start_time,
            end_time,
            args.min_confidence,
            args.min_gap,
            args.verbose,
        )
        duration = end_time

        if not cuts:
            sys.exit(0)

        if args.dry_run:
            log("[Dry run - no files created]")
            return

        log(f"Splitting to: {video_subdir}")
        output_files = split_video(args.input, video_subdir, cuts, duration, log=log)
        log("")

        # Save splits.json with all detection data
        boundaries = [0.0] + [c.time for c in cuts] + [duration]
        splits_file = SplitsFile(
            source_file=args.input.name,
            duration=duration,
            processed_date=datetime.now().isoformat(),
            parameters=SplitParameters(min_confidence=args.min_confidence, min_gap=args.min_gap),
            detection=DetectionData(
                scenes=[SceneDetection(time=t, score=s) for t, s in result.scenes],
                blacks=[BlackDetection(end_time=t, duration=d) for t, d in result.blacks],
                audio_changes=[
                    AudioChange(time=t, step=s) for t, s in result.audio_changes.items()
                ],
            ),
            candidates=[
                CandidateInfo(
                    time=c.time,
                    scene_score=c.scene_score,
                    black_duration=c.black_duration,
                    audio_step=c.audio_step,
                    confidence_score=c.confidence_score,
                    selected=c in cuts,
                )
                for c in sorted(all_candidates, key=lambda x: x.time)
            ],
            segments=[
                SegmentInfo(
                    index=i + 1,
                    start=boundaries[i],
                    end=boundaries[i + 1],
                    output_file=output_files[i].name,
                )
                for i in range(len(output_files))
            ],
        )
        splits_file.save(video_subdir / "splits.json")

        clips = process_clips(
            video_subdir,
            output_files,
            transcribe=not args.skip_transcribe,
            workers=args.workers,
            transcribe_workers=args.transcribe_workers,
            log=log,
        )
        metadata = VideoMetadata(
            source_file=args.input.name, processed_date=datetime.now().isoformat(), clips=clips
        )
        metadata.save(metadata_path)

        generate_gallery(args.output_dir, transcribe=not args.skip_transcribe, log=log)
        log("")
        log("Done!")


def cmd_serve(args):
    """Start web server for viewing and editing."""
    from .server import run_server

    if not args.output_dir.exists():
        print(f"Error: Output directory not found: {args.output_dir}", file=sys.stderr)
        sys.exit(1)
    run_server(args.output_dir, host=args.host, port=args.port, regenerate=args.regenerate)


def cmd_preprocess(args):
    """Convert DV/film scan files to MP4."""
    if not args.input.is_dir():
        print(f"Error: Input must be a directory: {args.input}", file=sys.stderr)
        sys.exit(1)

    args.target_dir.mkdir(parents=True, exist_ok=True)
    input_files = sorted(
        f for f in args.input.iterdir() if f.is_file() and f.suffix.lower() in (".avi", ".mp4")
    )

    if not input_files:
        print(f"No .avi or .mp4 files found in {args.input}")
        sys.exit(0)

    # Partition into skip/convert
    to_convert = []
    for src in input_files:
        dst_path = args.target_dir / f"{src.stem}.mp4"
        if dst_path.exists():
            print(f"Skip (exists): {src.name}")
        else:
            to_convert.append((src, dst_path))

    if not to_convert:
        print("All files already converted")
        sys.exit(0)

    def get_processor(src: Path):
        if args.type == "dv":
            return preprocess_dv_file
        elif args.type == "film-scan":
            return preprocess_film_scan
        # Auto-detect from extension
        return preprocess_dv_file if src.suffix.lower() == ".avi" else preprocess_film_scan

    cpu_count = os.cpu_count() or 4
    default_workers = max(1, cpu_count // 2)
    workers = args.workers if args.workers > 0 else default_workers
    workers = min(workers, len(to_convert))  # no more workers than files
    threads = max(1, cpu_count // workers)
    print(f"Converting {len(to_convert)} files ({workers} workers, {threads} threads each)...")

    def convert_one(item: tuple[Path, Path]) -> str:
        src, dst = item
        processor = get_processor(src)
        processor(src, dst, threads=threads)
        return src.name

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(convert_one, item): item for item in to_convert}
        for i, future in enumerate(as_completed(futures), 1):
            src, _ = futures[future]
            try:
                future.result()
                print(f"  [{i}/{len(to_convert)}] {src.name}")
            except Exception as e:
                print(f"  [{i}/{len(to_convert)}] {src.name} FAILED: {e}")

    print("Done!")


def cmd_transcribe(args):
    """Transcribe existing clips in output directory."""
    subdirs = find_video_subdirs(args.output_dir)
    if not subdirs:
        print("No video subdirectories found")
        sys.exit(1)

    ffmpeg_workers = args.workers if args.workers > 0 else get_default_workers()
    transcribe_workers = args.transcribe_workers
    total = sum(len(videos) for _, videos in subdirs)
    print(f"Transcribing {total} videos in {len(subdirs)} subdirectories")
    print(f"  ffmpeg workers={ffmpeg_workers}, transcribe workers={transcribe_workers}")

    for subdir, videos in subdirs:
        print(f"\n{subdir.name}:")
        mp4_files = [convert_to_mp4(v) for v in sorted(videos)]

        # Filter to only files needing transcription
        to_transcribe = [f for f in mp4_files if not f.with_suffix(".txt").exists()]
        if not to_transcribe:
            print("  All files already transcribed")
        else:
            # Phase 1: Extract audio in parallel
            print(f"  Extracting audio for {len(to_transcribe)} files...")
            wav_map = {}
            with ThreadPoolExecutor(max_workers=ffmpeg_workers) as executor:
                futures = {executor.submit(extract_audio, f): f for f in to_transcribe}
                for future in as_completed(futures):
                    mp4 = futures[future]
                    try:
                        wav_map[mp4] = future.result()
                    except Exception as e:
                        print(f"    Error extracting audio for {mp4.name}: {e}")

            # Phase 2: Transcribe (parallel if workers > 1)
            print(f"  Transcribing {len(wav_map)} files ({transcribe_workers} workers)...")
            try:
                if transcribe_workers == 1:
                    for i, (mp4, wav) in enumerate(wav_map.items(), 1):
                        print(f"    [{i}/{len(wav_map)}] {mp4.name}")
                        transcribe_from_wav(mp4, wav)
                else:
                    work_items = [(str(mp4), str(wav)) for mp4, wav in wav_map.items()]
                    ctx = multiprocessing.get_context("spawn")
                    pool = ctx.Pool(processes=transcribe_workers)
                    try:
                        for i, (video_path_str, _) in enumerate(
                            pool.imap_unordered(transcribe_worker, work_items), 1
                        ):
                            print(f"    [{i}/{len(wav_map)}] {Path(video_path_str).name}")
                    finally:
                        pool.close()
                        pool.join()
            except Exception:
                for wav in wav_map.values():
                    wav.unlink(missing_ok=True)
                raise

        metadata_path = subdir / "metadata.json"
        if metadata_path.exists():
            metadata = VideoMetadata.load(metadata_path)
            for clip in metadata.clips:
                txt_path = subdir / Path(clip.file).with_suffix(".txt").name
                if txt_path.exists():
                    clip.transcript = txt_path.read_text()
            metadata.save(metadata_path)

    print("\nDone!")


def cmd_gallery(args):
    """Regenerate gallery.html only."""
    if not args.output_dir.exists():
        print(f"Error: Output directory not found: {args.output_dir}", file=sys.stderr)
        sys.exit(1)
    generate_gallery(args.output_dir)
    print("Done!")


def cmd_frames(args):
    """Extract frames at a specific timestamp for debugging."""
    if not args.input.exists():
        print(f"Error: Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    try:
        timestamp = parse_timestamp(args.at)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    output_dir = args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get video fps for calculating frame timestamps
    fps = get_video_fps(args.input)
    video_stem = args.input.stem

    # Extract frames to temp files
    # Fast seek, then limit frame count based on fps * duration
    num_frames = int(fps * args.duration) + 1
    temp_pattern = output_dir / "frame_%04d.jpg"
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        str(timestamp),
        "-i",
        str(args.input),
        "-frames:v",
        str(num_frames),
        "-q:v",
        "2",
        str(temp_pattern),
    ]
    result = run_ffmpeg(cmd)
    if result.returncode != 0:
        print(f"Error extracting frames: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    # Rename frames with actual timestamps
    frame_files = sorted(output_dir.glob("frame_*.jpg"))
    renamed_files = []
    for i, frame_file in enumerate(frame_files):
        frame_time = timestamp + (i / fps)
        minutes = int(frame_time // 60)
        secs = int(frame_time % 60)
        millis = int((frame_time % 1) * 1000)
        new_name = f"{video_stem}_{minutes:02d}m{secs:02d}s{millis:03d}ms.jpg"
        new_path = output_dir / new_name
        frame_file.rename(new_path)
        renamed_files.append(new_path)

    print(f"Extracted {len(renamed_files)} frames:")
    for f in renamed_files:
        print(f"  {f}")


def main():
    parser = argparse.ArgumentParser(
        description="Video catalog: split recordings, transcribe, and generate gallery"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # process subcommand
    p_process = subparsers.add_parser(
        "process", help="Full pipeline: detect → split → transcribe → gallery"
    )
    p_process.add_argument("input", type=Path, help="Input video file")
    p_process.add_argument(
        "--output-dir", type=Path, default=Path("output"), help="Output directory (default: output)"
    )
    p_process.add_argument("--name", type=str, help="Override output subdirectory name")
    p_process.add_argument(
        "--min-confidence",
        type=int,
        default=12,
        help="Minimum confidence score for cuts (default: 12)",
    )
    p_process.add_argument(
        "--min-gap",
        type=float,
        default=1.0,
        help="Minimum gap between cuts in seconds (default: 1)",
    )
    p_process.add_argument(
        "--start", type=float, default=0, help="Start processing at N seconds (default: 0)"
    )
    p_process.add_argument(
        "--limit",
        type=float,
        default=0,
        help="Limit processing to N seconds from start (0=full video)",
    )
    p_process.add_argument(
        "--dry-run", action="store_true", help="Show detected cuts without splitting"
    )
    p_process.add_argument(
        "--verbose", "-v", action="store_true", help="Show detailed detection info"
    )
    p_process.add_argument(
        "--force", action="store_true", help="Force reprocessing even if already processed"
    )
    p_process.add_argument("--skip-transcribe", action="store_true", help="Skip transcription step")
    p_process.add_argument(
        "--workers",
        type=int,
        default=0,
        help="Number of parallel workers for ffmpeg (default: auto)",
    )
    p_process.add_argument(
        "--transcribe-workers",
        type=int,
        default=1,
        help="Number of parallel Whisper workers (default: 1, ~3GB RAM each)",
    )
    p_process.set_defaults(func=cmd_process)

    # serve subcommand
    p_serve = subparsers.add_parser("serve", help="Start web server for viewing and editing")
    p_serve.add_argument(
        "--output-dir", type=Path, default=Path("output"), help="Output directory (default: output)"
    )
    p_serve.add_argument("--host", default="127.0.0.1", help="Host (default: 127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=8000, help="Port (default: 8000)")
    p_serve.add_argument(
        "--regenerate", action="store_true", help="Regenerate gallery HTML on each page load"
    )
    p_serve.set_defaults(func=cmd_serve)

    # preprocess subcommand
    p_preprocess = subparsers.add_parser("preprocess", help="Convert DV/film scan files to MP4")
    p_preprocess.add_argument(
        "input", type=Path, help="Input directory containing .avi or .mp4 files"
    )
    p_preprocess.add_argument(
        "--target-dir", type=Path, required=True, help="Target directory for converted files"
    )
    p_preprocess.add_argument(
        "--workers", type=int, default=0, help="Number of parallel workers (default: auto)"
    )
    p_preprocess.add_argument(
        "--type",
        choices=["dv", "film-scan"],
        help="Source type: dv (interlaced AVI) or film-scan (progressive MP4). Auto-detects from extension if not specified.",
    )
    p_preprocess.set_defaults(func=cmd_preprocess)

    # transcribe subcommand
    p_transcribe = subparsers.add_parser("transcribe", help="Transcribe existing clips")
    p_transcribe.add_argument(
        "--output-dir", type=Path, default=Path("output"), help="Output directory (default: output)"
    )
    p_transcribe.add_argument(
        "--workers",
        type=int,
        default=0,
        help="Number of parallel workers for ffmpeg (default: auto)",
    )
    p_transcribe.add_argument(
        "--transcribe-workers",
        type=int,
        default=1,
        help="Number of parallel Whisper workers (default: 1, ~3GB RAM each)",
    )
    p_transcribe.set_defaults(func=cmd_transcribe)

    # gallery subcommand
    p_gallery = subparsers.add_parser("gallery", help="Regenerate gallery.html only")
    p_gallery.add_argument(
        "--output-dir", type=Path, default=Path("output"), help="Output directory (default: output)"
    )
    p_gallery.set_defaults(func=cmd_gallery)

    # frames subcommand
    p_frames = subparsers.add_parser("frames", help="Extract frames at timestamp for debugging")
    p_frames.add_argument("input", type=Path, help="Input video file")
    p_frames.add_argument("--at", required=True, help="Timestamp (e.g., 47.5, 47m40s, 1h2m3s)")
    p_frames.add_argument(
        "--output", type=Path, default=Path("frames"), help="Output directory (default: frames)"
    )
    p_frames.add_argument(
        "--duration", type=float, default=1.0, help="Duration in seconds (default: 1.0)"
    )
    p_frames.set_defaults(func=cmd_frames)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
