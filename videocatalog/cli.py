"""Command-line interface for videocatalog."""

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

from .models import VideoMetadata
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
    process_clips,
    update_catalog,
)
from .gallery import generate_gallery


def main():
    parser = argparse.ArgumentParser(
        description="Split video at recording boundaries using multi-signal detection"
    )
    parser.add_argument("input", type=Path, nargs="?", help="Input video file")
    parser.add_argument("--output-dir", type=Path, required=True, help="Output directory")
    parser.add_argument("--min-confidence", type=int, default=45,
                        help="Minimum confidence score for cuts (default: 45)")
    parser.add_argument("--min-gap", type=float, default=10.0,
                        help="Minimum gap between cuts in seconds (default: 10)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show detected cuts without splitting")
    parser.add_argument("--gallery-only", action="store_true",
                        help="Only regenerate gallery from existing files in output-dir")
    parser.add_argument("--skip-transcribe", action="store_true",
                        help="Skip transcription step")
    parser.add_argument("--transcribe-only", action="store_true",
                        help="Only run transcription on existing videos in output-dir")
    parser.add_argument("--force", action="store_true",
                        help="Force reprocessing even if already processed")
    parser.add_argument("--serve", action="store_true",
                        help="Start web server for viewing and editing")
    parser.add_argument("--host", default="127.0.0.1",
                        help="Host for web server (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8000,
                        help="Port for web server (default: 8000)")

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
        run_server(args.output_dir, host=args.host, port=args.port)
        return

    if args.transcribe_only:
        subdirs = find_video_subdirs()
        if not subdirs:
            print("No video subdirectories found")
            sys.exit(1)

        total = sum(len(videos) for _, videos in subdirs)
        print(f"Transcribing {total} videos in {len(subdirs)} subdirectories")

        for subdir, videos in subdirs:
            print(f"\n{subdir.name}:")
            for video in sorted(videos):
                mp4_file = convert_to_mp4(video)
                print(f"  {mp4_file.name}")
                transcribe_video(mp4_file)

            metadata_path = subdir / "metadata.json"
            if metadata_path.exists():
                metadata = VideoMetadata.load(metadata_path)
                for clip in metadata.clips:
                    txt_path = subdir / clip.file.replace('.mp4', '.txt')
                    if txt_path.exists():
                        clip.transcript = txt_path.read_text()
                metadata.save(metadata_path)

        print("\nDone!")
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
            clips = process_clips(subdir, sorted(videos), transcribe=not args.skip_transcribe)
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

    video_name = args.input.stem
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
    print(f"Duration: {format_time(duration)}")
    print()

    print("Running detection...")
    scenes = detect_scenes(args.input)
    blacks = detect_black_frames(args.input)
    audio_changes = detect_audio_changes(args.input, duration)

    print(f"  Found {len(scenes)} scene changes, {len(blacks)} black frames, {len(audio_changes)} audio changes")
    print()

    print(f"Finding cuts (min_confidence={args.min_confidence}, min_gap={args.min_gap}s)...")
    cuts = find_cuts(scenes, blacks, audio_changes, args.min_confidence, args.min_gap)

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

    clips = process_clips(video_subdir, output_files, transcribe=not args.skip_transcribe)
    metadata = VideoMetadata(
        source_file=args.input.name,
        processed_date=datetime.now().isoformat(),
        clips=clips
    )
    metadata.save(metadata_path)

    update_catalog(args.output_dir, video_name, args.input.name, len(clips))

    generate_gallery(args.output_dir, transcribe=not args.skip_transcribe)
    print()
    print("Done!")


if __name__ == "__main__":
    main()
