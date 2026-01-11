"""Regression tests for scene split detection algorithm."""

import pytest

from videocatalog.processing import (
    detect_scenes,
    detect_black_frames,
    detect_audio_changes,
    find_cuts,
    verify_candidates,
    get_video_duration,
)

from .conftest import TEST_LIMIT


@pytest.mark.requires_videos
def test_scene_splits(test_videos_dir, golden):
    """Test that scene detection produces expected cuts."""
    video_path = test_videos_dir / golden["video_file"]
    if not video_path.exists():
        pytest.skip(f"Video not found: {video_path}")

    params = golden.get("parameters", {})
    start_time = golden.get("start_time", 0)
    end_time = golden.get("end_time", 0)

    # Get duration for audio analysis
    duration = get_video_duration(video_path)
    if end_time == 0:
        end_time = duration

    # Apply test limit if set
    if TEST_LIMIT > 0:
        end_time = min(end_time, start_time + TEST_LIMIT)

    # Run detection
    scenes = detect_scenes(video_path, start_time=start_time, end_time=end_time)
    blacks = detect_black_frames(video_path, start_time=start_time, end_time=end_time)
    audio = detect_audio_changes(video_path, duration, start_time=start_time, end_time=end_time)

    # Find cuts
    min_confidence = params.get("min_confidence", 12)
    min_gap = params.get("min_gap", 1.0)

    cuts, all_candidates, scene_max = find_cuts(
        scenes, blacks, audio,
        min_confidence=min_confidence,
        min_gap=min_gap,
        return_all=True
    )

    # Verify candidates (histogram check)
    verified = verify_candidates(video_path, cuts, scene_max)

    # Extract cut times
    actual_cuts = [c.time for c in verified]
    expected_cuts = [t for t in golden["expected_cuts"] if start_time <= t < end_time]

    assert actual_cuts == expected_cuts, (
        f"Cut mismatch for {golden['video_file']} ({start_time}s-{end_time}s):\n"
        f"  Expected: {expected_cuts}\n"
        f"  Actual:   {actual_cuts}"
    )
