"""Regression tests for scene split detection algorithm."""

import pytest

from videocatalog.processing import detect_cuts, get_video_duration

from .conftest import TEST_LIMIT


@pytest.mark.requires_videos
def test_scene_splits(test_videos_dir, golden):
    """Test that scene detection produces expected cuts."""
    video_path = test_videos_dir / golden["video_file"]
    if not video_path.exists():
        pytest.skip(f"Video not found: {video_path}")

    params = golden.get("parameters", {})
    start_time = golden.get("start_time", 0)
    end_time = golden.get("end_time", 0) or None

    # Apply test limit if set
    if TEST_LIMIT > 0:
        if end_time is None:
            end_time = get_video_duration(video_path)
        end_time = min(end_time, start_time + TEST_LIMIT)

    result = detect_cuts(
        video_path,
        start_time=start_time,
        end_time=end_time,
        min_confidence=params.get("min_confidence", 12),
        min_gap=params.get("min_gap", 1.0),
    )

    actual_cuts = [c.time for c in result.cuts]
    actual_end = end_time or result.duration
    expected_cuts = [t for t in golden["expected_cuts"] if start_time <= t < actual_end]

    assert actual_cuts == expected_cuts, (
        f"Cut mismatch for {golden['video_file']} ({start_time}s-{actual_end}s):\n"
        f"  Expected: {expected_cuts}\n"
        f"  Actual:   {actual_cuts}"
    )
