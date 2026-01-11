"""Command-line interface for videocatalog."""

import argparse
import multiprocessing
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from .models import (
    VideoMetadata,
    SplitsFile,
    SplitParameters,
    DetectionData,
    SceneDetection,
    BlackDetection,
    AudioChange,
    CandidateInfo,
    SegmentInfo,
)
from .processing import (
    detect_scenes,
    detect_black_frames,
    detect_audio_changes,
    get_video_duration,
    format_time,
    find_cuts,
    split_video,
    convert_to_mp4,
    transcribe_video,
    extract_audio,
    process_clips,
    update_catalog,
    verify_candidates,
    _get_default_workers,
    _transcribe_worker,
    _transcribe_from_wav,
)
from .gallery import generate_gallery


def main():
    parser = argparse.ArgumentParser(
        description="Split video at recording boundaries using multi-signal detection"
    )
    parser.add_argument("input", type=Path, nargs="?", help="Input video file")
    parser.add_argument("--output-dir", type=Path, required=True, help="Output directory")
    parser.add_argument("--min-confidence", type=int, default=12,
                        help="Minimum confidence score for cuts (default: 12)")
    parser.add_argument("--min-gap", type=float, default=1.0,
                        help="Minimum gap between cuts in seconds (default: 1)")
    parser.add_argument("--limit", type=float, default=0,
                        help="Limit processing to first N seconds (0=full video)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show detected cuts without splitting")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Show detailed detection info for tuning parameters")
    parser.add_argument("--gallery-only", action="store_true",
                        help="Reprocess clips and regenerate gallery")
    parser.add_argument("--html-only", action="store_true",
                        help="Only regenerate gallery HTML (fast)")
    parser.add_argument("--skip-transcribe", action="store_true",
                        help="Skip transcription step")
    parser.add_argument("--split-only", action="store_true",
                        help="Skip transcription (still generates thumbnails and gallery)")
    parser.add_argument("--transcribe-only", action="store_true",
                        help="Only run transcription on existing videos in output-dir")
    parser.add_argument("--name", type=str,
                        help="Override output subdirectory name (for testing different configs)")
    parser.add_argument("--force", action="store_true",
                        help="Force reprocessing even if already processed")
    parser.add_argument("--serve", action="store_true",
                        help="Start web server for viewing and editing")
    parser.add_argument("--regenerate", action="store_true",
                        help="Regenerate gallery HTML on each page load")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Host for web server (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000,
                        help="Port for web server (default: 8000)")
    parser.add_argument("--workers", type=int, default=0,
                        help="Number of parallel workers for ffmpeg (default: auto)")
    parser.add_argument("--transcribe-workers", type=int, default=1,
                        help="Number of parallel Whisper workers (default: 1, each uses ~3GB RAM)")

    args = parser.parse_args()

    def find_video_subdirs():
        if not args.output_dir.exists():
            print(f"Error: Output directory not found: {args.output_dir}", file=sys.stderr)
            sys.exit(1)

        subdirs = []
        for subdir in sorted(args.output_dir.iterdir()):
            if subdir.is_dir():
                extensions = {'.avi', '.mp4', '.mov', '.mkv', '.webm', '.wmv', '.flv'}
                videos = [f for f in subdir.iterdir()
                          if f.is_file() and f.suffix.lower() in extensions]
                if videos:
                    subdirs.append((subdir, videos))
        return subdirs

    if args.serve:
        from .server import run_server
        if not args.output_dir.exists():
            print(f"Error: Output directory not found: {args.output_dir}", file=sys.stderr)
            sys.exit(1)
        run_server(args.output_dir, host=args.host, port=args.port, regenerate=args.regenerate)
        return

    if args.transcribe_only:
        subdirs = find_video_subdirs()
        if not subdirs:
            print("No video subdirectories found")
            sys.exit(1)

        ffmpeg_workers = args.workers if args.workers > 0 else _get_default_workers()
        transcribe_workers = args.transcribe_workers
        total = sum(len(videos) for _, videos in subdirs)
        print(f"Transcribing {total} videos in {len(subdirs)} subdirectories")
        print(f"  ffmpeg workers={ffmpeg_workers}, transcribe workers={transcribe_workers}")

        for subdir, videos in subdirs:
            print(f"\n{subdir.name}:")
            mp4_files = [convert_to_mp4(v) for v in sorted(videos)]

            # Filter to only files needing transcription
            to_transcribe = [f for f in mp4_files if not f.with_suffix('.txt').exists()]
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
                            _transcribe_from_wav(mp4, wav)
                    else:
                        work_items = [(str(mp4), str(wav)) for mp4, wav in wav_map.items()]
                        ctx = multiprocessing.get_context('spawn')
                        pool = ctx.Pool(processes=transcribe_workers)
                        try:
                            for i, (video_path_str, _) in enumerate(pool.imap_unordered(_transcribe_worker, work_items), 1):
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
                    txt_path = subdir / Path(clip.file).with_suffix('.txt').name
                    if txt_path.exists():
                        clip.transcript = txt_path.read_text()
                metadata.save(metadata_path)

        print("\nDone!")
        return

    if args.html_only:
        if not args.output_dir.exists():
            print(f"Error: Output directory not found: {args.output_dir}", file=sys.stderr)
            sys.exit(1)
        generate_gallery(args.output_dir)
        print("Done!")
        return

    if args.gallery_only:
        subdirs = find_video_subdirs()
        if not subdirs:
            print("No video subdirectories found")
            sys.exit(1)

        print(f"Processing {len(subdirs)} video subdirectories...")
        for subdir, videos in subdirs:
            metadata_path = subdir / "metadata.json"

            print(f"\n{subdir.name}:")
            clips = process_clips(subdir, sorted(videos), transcribe=not args.skip_transcribe, workers=args.workers, transcribe_workers=args.transcribe_workers)
            metadata = VideoMetadata(
                source_file=subdir.name,
                processed_date=datetime.now().isoformat(),
                clips=clips
            )
            metadata.save(metadata_path)
            update_catalog(args.output_dir, subdir.name, subdir.name, len(clips))

        generate_gallery(args.output_dir, transcribe=not args.skip_transcribe)
        print("\nDone!")
        return

    if not args.input:
        print("Error: input file required (unless using --gallery-only)", file=sys.stderr)
        sys.exit(1)

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

    print(f"Analyzing: {args.input}")
    print()

    duration = get_video_duration(args.input)
    limit = args.limit if args.limit > 0 else duration
    if limit < duration:
        print(f"Duration: {format_time(duration)} (limiting to {format_time(limit)})")
        duration = limit
    else:
        print(f"Duration: {format_time(duration)}")
    print()

    print("Running detection...")
    scenes = detect_scenes(args.input, limit=limit)
    blacks = detect_black_frames(args.input, limit=limit)
    audio_changes = detect_audio_changes(args.input, duration, limit=limit)

    print(f"  Found {len(scenes)} scene changes, {len(blacks)} black frames, {len(audio_changes)} audio changes")

    if args.verbose:
        print()
        print("=== RAW DETECTION DATA ===")
        print()
        print("Scene changes (time, score) - threshold >=5:")
        for time, score in sorted(scenes):
            marker = " ***" if score >= 15 else " **" if score >= 10 else " *" if score >= 6 else ""
            print(f"  {format_time(time)} score={score:5.1f}{marker}")
        print()
        print("Black frames (end_time, duration) - all >=0.1s:")
        for end_time, dur in sorted(blacks):
            marker = " ***" if dur >= 1.0 else " **" if dur >= 0.5 else " *" if dur >= 0.2 else ""
            print(f"  {format_time(end_time)} dur={dur:.2f}s{marker}")
        print()
        print("Audio level jumps (time, step_dB) - threshold >10dB:")
        for t in sorted(audio_changes.keys()):
            step = audio_changes[t]
            marker = " ***" if step >= 25 else " **" if step >= 18 else " *" if step >= 12 else ""
            print(f"  {format_time(t)} step={step:5.1f}dB{marker}")
        print()
        print("Legend: * = low score, ** = medium, *** = high")
        print()

    print()
    print(f"Finding cuts (min_confidence={args.min_confidence}, min_gap={args.min_gap}s)...")
    cuts, all_candidates, scene_max_scores = find_cuts(scenes, blacks, audio_changes, args.min_confidence, args.min_gap, return_all=True)

    # Verify cuts with histogram comparison (filters borderline false positives)
    if cuts:
        print(f"Verifying {len(cuts)} cut(s) with histogram comparison...")
        cuts = verify_candidates(args.input, cuts, scene_max_scores, verbose=args.verbose)

    if args.verbose:
        print()
        print("=== ALL CANDIDATES (chronological) ===")
        print("Scoring: scene(0-40) + black(0-35) + audio(0-30) = max 105")
        print()
        for c in sorted(all_candidates, key=lambda x: x.time):
            selected = "SELECTED" if c in cuts else f"skip (below {args.min_confidence})" if c.confidence_score < args.min_confidence else "skip (too close)"
            s, b, a = c.score_breakdown()
            score_str = f"[{c.confidence_score:3d}={s:2d}+{b:2d}+{a:2d}]"
            print(f"  {format_time(c.time)} {score_str} {c.signal_summary():40s} -> {selected}")
        print()

    if not cuts:
        print("No cuts detected. Try lowering --min-confidence")
        sys.exit(0)

    print(f"\nFound {len(cuts)} cut(s):")
    for cut in cuts:
        print(f"  {format_time(cut.time)} [score:{cut.confidence_score:3d}] ({cut.signal_summary()})")
    print()

    num_segments = len(cuts) + 1
    print(f"Will create {num_segments} segment(s)")
    print()

    if args.dry_run:
        print("[Dry run - no files created]")
        return

    print(f"Splitting to: {video_subdir}")
    output_files = split_video(args.input, video_subdir, cuts, duration)
    print()

    # Save splits.json with all detection data
    boundaries = [0.0] + [c.time for c in cuts] + [duration]
    splits_file = SplitsFile(
        source_file=args.input.name,
        duration=duration,
        processed_date=datetime.now().isoformat(),
        parameters=SplitParameters(
            min_confidence=args.min_confidence,
            min_gap=args.min_gap
        ),
        detection=DetectionData(
            scenes=[SceneDetection(time=t, score=s) for t, s in scenes],
            blacks=[BlackDetection(end_time=t, duration=d) for t, d in blacks],
            audio_changes=[AudioChange(time=t, step=s) for t, s in audio_changes.items()]
        ),
        candidates=[
            CandidateInfo(
                time=c.time,
                scene_score=c.scene_score,
                black_duration=c.black_duration,
                audio_step=c.audio_step,
                confidence_score=c.confidence_score,
                selected=c in cuts
            ) for c in sorted(all_candidates, key=lambda x: x.time)
        ],
        segments=[
            SegmentInfo(
                index=i + 1,
                start=boundaries[i],
                end=boundaries[i + 1],
                output_file=output_files[i].name
            ) for i in range(len(output_files))
        ]
    )
    splits_file.save(video_subdir / "splits.json")

    skip_transcribe = args.skip_transcribe or args.split_only
    clips = process_clips(video_subdir, output_files, transcribe=not skip_transcribe, workers=args.workers, transcribe_workers=args.transcribe_workers)
    metadata = VideoMetadata(
        source_file=args.input.name,
        processed_date=datetime.now().isoformat(),
        clips=clips
    )
    metadata.save(metadata_path)

    update_catalog(args.output_dir, video_name, args.input.name, len(clips))

    generate_gallery(args.output_dir, transcribe=not skip_transcribe)
    print()
    print("Done!")


if __name__ == "__main__":
    main()
