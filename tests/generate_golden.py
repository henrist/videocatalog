#!/usr/bin/env python3
"""Generate golden file for scene split regression tests.

Usage:
    uv run python -m tests.generate_golden video.avi [--start 0] [--end 180]
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from videocatalog.processing import detect_cuts


def main():
    parser = argparse.ArgumentParser(description="Generate golden file for regression tests")
    parser.add_argument("video", help="Video filename (relative to VIDEOCATALOG_TEST_VIDEOS)")
    parser.add_argument("--start", type=float, default=0, help="Start time in seconds")
    parser.add_argument("--end", type=float, default=0, help="End time in seconds (0=full)")
    parser.add_argument("--min-confidence", type=int, default=12)
    parser.add_argument("--min-gap", type=float, default=1.0)
    parser.add_argument("--output", help="Output filename (default: derived from video name)")
    args = parser.parse_args()

    # Resolve video path
    test_dir = os.environ.get("VIDEOCATALOG_TEST_VIDEOS", os.getcwd())

    video_path = Path(test_dir) / args.video
    if not video_path.exists():
        print(f"Error: Video not found: {video_path}")
        sys.exit(1)

    print(f"Processing: {video_path}")

    end_time = args.end if args.end > 0 else None

    result = detect_cuts(
        video_path,
        start_time=args.start,
        end_time=end_time,
        min_confidence=args.min_confidence,
        min_gap=args.min_gap,
        verbose=True,
    )

    actual_end = end_time or result.duration
    segment_duration = actual_end - args.start
    print(f"  Analyzed: {args.start}s - {actual_end}s ({segment_duration:.1f}s of {result.duration:.1f}s total)")
    print(f"  Scenes: {len(result.scenes)}, Blacks: {len(result.blacks)}, Audio changes: {len(result.audio_changes)}")

    cut_times = [c.time for c in result.cuts]

    print(f"\nFound {len(cut_times)} cuts:")
    for t in cut_times:
        print(f"  {t:.3f}s")

    # Build golden file
    golden = {
        "video_file": args.video,
        "start_time": args.start,
        "end_time": args.end,
        "parameters": {
            "min_confidence": args.min_confidence,
            "min_gap": args.min_gap,
        },
        "expected_cuts": cut_times,
    }

    # Write output
    golden_dir = Path(__file__).parent / "golden"
    golden_dir.mkdir(exist_ok=True)

    output_name = args.output or Path(args.video).stem + ".json"
    output_path = golden_dir / output_name
    output_path.write_text(json.dumps(golden, indent=2) + "\n")

    print(f"\nWritten: {output_path}")


if __name__ == "__main__":
    main()
