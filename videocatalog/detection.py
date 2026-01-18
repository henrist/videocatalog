"""Cut detection: scene changes, black frames, audio changes, and verification."""

import os
import re
import subprocess
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from typing import Literal, overload

from .models import CutCandidate, CutDetectionResult, NoiseZone
from .utils import format_time, get_video_duration


def detect_scenes(
    video_path: Path, limit: float = 0, start_time: float = 0, end_time: float = 0
) -> list[tuple[float, float]]:
    """Detect scene changes using FFmpeg's scdet filter.

    Args:
        video_path: Path to video file
        limit: Duration limit (deprecated, use end_time instead)
        start_time: Start time in seconds (seek before input for speed)
        end_time: End time in seconds (0 = full video)
    """
    print("  Detecting scene changes...")
    cmd = ["ffmpeg"]
    if start_time > 0:
        cmd += ["-ss", str(start_time)]
    duration = end_time - start_time if end_time > 0 else limit
    if duration > 0:
        cmd += ["-t", str(duration)]
    cmd += ["-i", str(video_path), "-vf", "histeq,scdet=threshold=0.1", "-f", "null", "-"]
    result = subprocess.run(cmd, capture_output=True, text=True)

    pattern = r"lavfi\.scd\.score:\s*([\d.]+),\s*lavfi\.scd\.time:\s*([\d.]+)"
    scenes = []
    for match in re.finditer(pattern, result.stderr):
        score = float(match.group(1))
        time = float(match.group(2)) + start_time  # Convert to absolute time
        if score >= 5:
            scenes.append((time, score))

    return scenes


def detect_black_frames(
    video_path: Path, limit: float = 0, start_time: float = 0, end_time: float = 0
) -> list[tuple[float, float]]:
    """Detect black frames using FFmpeg's blackdetect filter.

    Args:
        video_path: Path to video file
        limit: Duration limit (deprecated, use end_time instead)
        start_time: Start time in seconds (seek before input for speed)
        end_time: End time in seconds (0 = full video)
    """
    print("  Detecting black frames...")
    cmd = ["ffmpeg"]
    if start_time > 0:
        cmd += ["-ss", str(start_time)]
    duration = end_time - start_time if end_time > 0 else limit
    if duration > 0:
        cmd += ["-t", str(duration)]
    cmd += ["-i", str(video_path), "-vf", "blackdetect=d=0.1:pix_th=0.10", "-an", "-f", "null", "-"]
    result = subprocess.run(cmd, capture_output=True, text=True)

    pattern = r"black_start:([\d.]+)\s+black_end:([\d.]+)\s+black_duration:([\d.]+)"
    blacks = []
    for match in re.finditer(pattern, result.stderr):
        black_end = float(match.group(2)) + start_time  # Convert to absolute time
        black_duration = float(match.group(3))
        if black_duration >= 0.1:
            blacks.append((black_end, black_duration))

    return blacks


def detect_audio_changes(
    video_path: Path, duration: float, limit: float = 0, start_time: float = 0, end_time: float = 0
) -> dict[int, float]:
    """Compute per-second RMS levels and detect large changes.

    Args:
        video_path: Path to video file
        duration: Total video duration (used for progress, not limiting)
        limit: Duration limit (deprecated, use end_time instead)
        start_time: Start time in seconds (seek before input for speed)
        end_time: End time in seconds (0 = full video)
    """
    print("  Analyzing audio levels...")
    rms_file = Path(tempfile.gettempdir()) / f"rms_analysis_{os.getpid()}.txt"

    cmd = ["ffmpeg"]
    if start_time > 0:
        cmd += ["-ss", str(start_time)]
    segment_duration = end_time - start_time if end_time > 0 else limit
    if segment_duration > 0:
        cmd += ["-t", str(segment_duration)]
    cmd += [
        "-i",
        str(video_path),
        "-af",
        f"asetnsamples=n=48000,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file={rms_file}",
        "-f",
        "null",
        "-",
    ]
    subprocess.run(cmd, capture_output=True, text=True)

    rms = {}
    try:
        if rms_file.exists():
            content = rms_file.read_text()
            pattern = r"pts_time:(\d+)\s*\n.*?RMS_level=([-\d.inf]+)"
            for match in re.finditer(pattern, content):
                t = int(match.group(1)) + int(start_time)  # Convert to absolute time
                level_str = match.group(2)
                if level_str == "-inf" or level_str == "-":
                    continue
                level = float(level_str)
                rms[t] = level
    finally:
        rms_file.unlink(missing_ok=True)

    changes = {}
    sorted_times = sorted(rms.keys())
    for i in range(1, len(sorted_times)):
        t = sorted_times[i]
        prev_t = sorted_times[i - 1]
        step = abs(rms[t] - rms[prev_t])
        if step > 5:
            changes[t] = step

    return changes


def verify_scene_change(
    video_path: Path, time: float, threshold: float = 0.7
) -> tuple[bool, float]:
    """Verify scene change by comparing color histograms before/after.

    Uses histogram comparison which is robust to camera motion.
    Returns (is_valid, similarity). A real scene change has low similarity.
    A smooth transition (same scene) has high similarity.
    """
    import cv2

    before_time = max(0, time - 0.5)
    after_time = time + 0.5

    # Extract two frames to temp files
    tmp_dir = Path(tempfile.gettempdir())
    frame1 = tmp_dir / f"hist_before_{os.getpid()}.png"
    frame2 = tmp_dir / f"hist_after_{os.getpid()}.png"

    try:
        # Extract frame before
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(before_time),
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-f",
                "image2",
                str(frame1),
            ],
            capture_output=True,
        )

        # Extract frame after
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(after_time),
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-f",
                "image2",
                str(frame2),
            ],
            capture_output=True,
        )

        if not frame1.exists() or not frame2.exists():
            return True, 0.0

        # Load images and convert to HSV
        img1 = cv2.imread(str(frame1))
        img2 = cv2.imread(str(frame2))
        if img1 is None or img2 is None:
            return True, 0.0

        hsv1 = cv2.cvtColor(img1, cv2.COLOR_BGR2HSV)
        hsv2 = cv2.cvtColor(img2, cv2.COLOR_BGR2HSV)

        # Calculate and normalize histograms
        hist1 = cv2.calcHist([hsv1], [0, 1], None, [50, 60], [0, 180, 0, 256])
        hist2 = cv2.calcHist([hsv2], [0, 1], None, [50, 60], [0, 180, 0, 256])
        cv2.normalize(hist1, hist1)
        cv2.normalize(hist2, hist2)

        # Compare histograms (1.0 = identical, 0 = no correlation)
        similarity = cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)

        # High similarity = same scene = should NOT cut (false positive)
        # Low similarity = different scene = real cut
        return similarity < threshold, similarity

    finally:
        frame1.unlink(missing_ok=True)
        frame2.unlink(missing_ok=True)


def check_scene_stability(
    video_path: Path, time: float, threshold: float = 0.955
) -> tuple[bool, float, float]:
    """Check if distant frames before/after cut are similar (flash detection).

    Compares frames at ±0.5s and ±2s. If BOTH intervals show high similarity
    (>=0.9), the cut is likely a flash/disturbance.

    Returns (is_flash, sim_1s, sim_2s).
    """
    import cv2

    def compare_frames(before_time: float, after_time: float) -> float:
        tmp_dir = Path(tempfile.gettempdir())
        frame1 = tmp_dir / f"stab_before_{os.getpid()}.png"
        frame2 = tmp_dir / f"stab_after_{os.getpid()}.png"

        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(max(0, before_time)),
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    "-f",
                    "image2",
                    str(frame1),
                ],
                capture_output=True,
            )
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(after_time),
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    "-f",
                    "image2",
                    str(frame2),
                ],
                capture_output=True,
            )

            if not frame1.exists() or not frame2.exists():
                return 0.0

            img1 = cv2.imread(str(frame1))
            img2 = cv2.imread(str(frame2))
            if img1 is None or img2 is None:
                return 0.0

            hsv1 = cv2.cvtColor(img1, cv2.COLOR_BGR2HSV)
            hsv2 = cv2.cvtColor(img2, cv2.COLOR_BGR2HSV)
            hist1 = cv2.calcHist([hsv1], [0, 1], None, [50, 60], [0, 180, 0, 256])
            hist2 = cv2.calcHist([hsv2], [0, 1], None, [50, 60], [0, 180, 0, 256])
            cv2.normalize(hist1, hist1)
            cv2.normalize(hist2, hist2)

            return cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)
        finally:
            frame1.unlink(missing_ok=True)
            frame2.unlink(missing_ok=True)

    # Check at multiple intervals to catch both momentary flashes and sustained flickering
    sim_short = compare_frames(time - 0.5, time + 0.5)
    sim_long = compare_frames(time - 2.0, time + 2.0)

    # Flash if EITHER interval is very high AND both are at least moderately high
    # This avoids filtering real cuts that have one high but one low interval
    is_flash = (sim_short >= threshold or sim_long >= threshold) and min(
        sim_short, sim_long
    ) >= 0.85
    return is_flash, sim_short, sim_long


def check_side_stability(
    video_path: Path, time: float, threshold: float = 0.7
) -> tuple[bool, float, float]:
    """Check if at least one side of cut has stable/similar frames.

    At a real cut, frames before the cut should be similar to each other,
    OR frames after the cut should be similar to each other.
    Camera motion has instability on BOTH sides.

    Returns (has_stable_side, sim_before, sim_after).
    """
    import cv2

    def compare_histogram(img1, img2) -> float:
        if img1 is None or img2 is None:
            return 0.0
        hsv1 = cv2.cvtColor(img1, cv2.COLOR_BGR2HSV)
        hsv2 = cv2.cvtColor(img2, cv2.COLOR_BGR2HSV)
        hist1 = cv2.calcHist([hsv1], [0, 1], None, [50, 60], [0, 180, 0, 256])
        hist2 = cv2.calcHist([hsv2], [0, 1], None, [50, 60], [0, 180, 0, 256])
        cv2.normalize(hist1, hist1)
        cv2.normalize(hist2, hist2)
        return cv2.compareHist(hist1, hist2, cv2.HISTCMP_CORREL)

    tmp_dir = Path(tempfile.gettempdir())
    frames = {}
    times = {
        "before_far": max(0, time - 2.0),
        "before_near": max(0, time - 0.5),
        "after_near": time + 0.5,
        "after_far": time + 2.0,
    }

    try:
        for name, t in times.items():
            path = tmp_dir / f"side_{name}_{os.getpid()}.png"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    str(t),
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    "-f",
                    "image2",
                    str(path),
                ],
                capture_output=True,
            )
            frames[name] = cv2.imread(str(path)) if path.exists() else None

        # Compare frames on each side
        sim_before = compare_histogram(frames["before_far"], frames["before_near"])
        sim_after = compare_histogram(frames["after_near"], frames["after_far"])

        # At least one side should be stable for a real cut
        has_stable_side = sim_before >= threshold or sim_after >= threshold
        return has_stable_side, sim_before, sim_after

    finally:
        for name in times:
            path = tmp_dir / f"side_{name}_{os.getpid()}.png"
            path.unlink(missing_ok=True)


def is_near_noise_zone(
    time: float, noise_zones: list[NoiseZone] | None, margin: float = 10.0
) -> bool:
    """Check if timestamp is within margin of any noise zone."""
    if not noise_zones:
        return False
    return any(zone.start - margin <= time <= zone.end + margin for zone in noise_zones)


def verify_candidates(
    video_path: Path,
    candidates: list[CutCandidate],
    scene_max_scores: dict[int, float],
    noise_zones: list[NoiseZone] | None = None,
    threshold: float = 0.7,
    verbose: bool = False,
    log_file=None,
) -> list[CutCandidate]:
    """Filter candidates by verifying with histogram comparison, flash and side stability.

    - Black frame corroboration: pass without checks (strong signal)
    - Near noise zones: histogram verification required
    - Audio corroboration: side stability + flash check (catches camera motion)
    - Scene-only: histogram + stability checks
    """

    def log(msg: str):
        if verbose:
            print(msg)
        if log_file:
            log_file.write(msg + "\n")

    log(f"  Verifying {len(candidates)} candidates with histogram comparison...")

    verified = []
    for c in candidates:
        max_score = scene_max_scores.get(int(c.time), 0)
        has_black = c.black_duration >= 0.2
        has_audio = c.audio_step >= 5
        near_noise = is_near_noise_zone(c.time, noise_zones)

        # Black frame = strong signal, skip all checks
        if has_black:
            log(f"    {format_time(c.time)} max={max_score:.1f} -> PASS (black frame)")
            verified.append(c)
            continue

        # Near noise zones: apply histogram verification (catches VHS static)
        if near_noise:
            is_valid, similarity = verify_scene_change(video_path, c.time, threshold)
            if not is_valid:
                log(
                    f"    {format_time(c.time)} max={max_score:.1f} hist={similarity:.3f} -> FAIL (noise zone)"
                )
                continue

        # Audio corroboration: pass without histogram check, just flash check
        if has_audio:
            is_flash, sim_short, sim_long = check_scene_stability(video_path, c.time)
            if is_flash:
                log(
                    f"    {format_time(c.time)} max={max_score:.1f} stab={sim_short:.2f}/{sim_long:.2f} -> FAIL (flash)"
                )
                continue
            log(f"    {format_time(c.time)} max={max_score:.1f} -> PASS (audio)")
            verified.append(c)
            continue

        # Borderline scene-only detections need histogram check
        if max_score < 10:
            is_valid, similarity = verify_scene_change(video_path, c.time, threshold)
            if not is_valid:
                log(
                    f"    {format_time(c.time)} max={max_score:.1f} hist={similarity:.3f} -> FAIL (same scene)"
                )
                continue

        # Flash detection for scene-only candidates
        is_flash, sim_short, sim_long = check_scene_stability(video_path, c.time)
        if is_flash:
            log(
                f"    {format_time(c.time)} max={max_score:.1f} stab={sim_short:.2f}/{sim_long:.2f} -> FAIL (flash)"
            )
            continue

        log(
            f"    {format_time(c.time)} max={max_score:.1f} stab={sim_short:.2f}/{sim_long:.2f} -> PASS"
        )
        verified.append(c)

    log(f"  Verified: {len(verified)}/{len(candidates)} candidates passed")

    return verified


def detect_noise_zones(
    scenes: list[tuple[float, float]],
    window_size: int = 10,
    avg_threshold: float = 2.5,
    min_duration: float = 10.0,
    merge_gap: float = 5.0,
) -> list[NoiseZone]:
    """Detect noise zones by sustained high scene detection density.

    Uses sliding window to detect regions with consistently elevated detection rates,
    even when individual seconds vary (e.g., VHS noise with periodic spikes).

    Args:
        scenes: List of (time, score) scene detections
        window_size: Size of sliding window in seconds
        avg_threshold: Average detections per second threshold within window
        min_duration: Minimum seconds to form a noise zone
        merge_gap: Merge zones within this many seconds
    """
    if not scenes:
        return []

    detections_per_sec = Counter(int(t) for t, _ in scenes)

    if not detections_per_sec:
        return []

    # Get time range
    min_t = min(detections_per_sec.keys())
    max_t = max(detections_per_sec.keys())

    # Sliding window: find seconds where average density exceeds threshold
    high_density_secs = []
    for t in range(min_t, max_t - window_size + 2):
        window_total = sum(detections_per_sec.get(t + i, 0) for i in range(window_size))
        avg = window_total / window_size
        if avg >= avg_threshold:
            high_density_secs.append(t)

    if not high_density_secs:
        return []

    # Group consecutive seconds into zones
    zones: list[NoiseZone] = []
    zone_start = high_density_secs[0]
    zone_end = high_density_secs[0]

    for t in high_density_secs[1:]:
        if t <= zone_end + 1:
            zone_end = t
        else:
            zone_duration = zone_end - zone_start + window_size
            if zone_duration >= min_duration:
                zone_count = sum(
                    detections_per_sec.get(zone_start + i, 0) for i in range(zone_duration)
                )
                zones.append(
                    NoiseZone(float(zone_start), float(zone_start + zone_duration), zone_count)
                )
            zone_start = t
            zone_end = t

    # Don't forget last zone
    zone_duration = zone_end - zone_start + window_size
    if zone_duration >= min_duration:
        zone_count = sum(detections_per_sec.get(zone_start + i, 0) for i in range(zone_duration))
        zones.append(NoiseZone(float(zone_start), float(zone_start + zone_duration), zone_count))

    # Merge zones within merge_gap
    if len(zones) > 1:
        merged = [zones[0]]
        for z in zones[1:]:
            if z.start - merged[-1].end <= merge_gap:
                total_count = merged[-1].detection_count + z.detection_count
                merged[-1] = NoiseZone(merged[-1].start, z.end, total_count)
            else:
                merged.append(z)
        zones = merged

    return zones


def suppress_noise_detections(
    scenes: list[tuple[float, float]], noise_zones: list[NoiseZone], boundary_margin: float = 5.0
) -> list[tuple[float, float]]:
    """Remove scene detections inside noise zones, keeping boundary detections.

    Args:
        scenes: List of (time, score) scene detections
        noise_zones: Detected noise zones
        boundary_margin: Keep detections within this margin of zone boundaries (5s default)
    """
    if not noise_zones:
        return scenes

    filtered = []
    for time, score in scenes:
        in_noise = False
        for zone in noise_zones:
            # Check if inside zone but outside boundary margins
            if zone.start + boundary_margin < time < zone.end - boundary_margin:
                in_noise = True
                break
        if not in_noise:
            filtered.append((time, score))

    return filtered


@overload
def find_cuts(
    scenes: list[tuple[float, float]],
    blacks: list[tuple[float, float]],
    audio_changes: dict[int, float],
    min_confidence: int,
    min_gap: float = 10.0,
    window: int = 2,
    return_all: Literal[False] = False,
) -> list[CutCandidate]: ...


@overload
def find_cuts(
    scenes: list[tuple[float, float]],
    blacks: list[tuple[float, float]],
    audio_changes: dict[int, float],
    min_confidence: int,
    min_gap: float = 10.0,
    window: int = 2,
    return_all: Literal[True] = ...,
) -> tuple[list[CutCandidate], list[CutCandidate], dict[int, float], list[NoiseZone]]: ...


def find_cuts(
    scenes: list[tuple[float, float]],
    blacks: list[tuple[float, float]],
    audio_changes: dict[int, float],
    min_confidence: int,
    min_gap: float = 10.0,
    window: int = 2,
    return_all: bool = False,
) -> (
    list[CutCandidate]
    | tuple[list[CutCandidate], list[CutCandidate], dict[int, float], list[NoiseZone]]
):
    """Combine signals to find recording boundaries.

    Uses cluster totals: sum of all scene scores in same second.
    This better identifies recording boundaries which often have
    multiple rapid detections.
    """
    # Detect noise zones before processing
    noise_zones = detect_noise_zones(scenes)

    # Suppress detections inside noise zones (keep boundary detections)
    filtered_scenes = suppress_noise_detections(scenes, noise_zones)

    # Group scenes by second - keep all detections for cluster analysis
    scene_clusters: dict[int, list[tuple[float, float]]] = defaultdict(list)
    for time, score in filtered_scenes:
        t = int(time)
        scene_clusters[t].append((time, score))

    # Compute cluster totals, max scores, and best time for each second
    scene_totals: dict[int, float] = {}
    scene_max: dict[int, float] = {}
    scene_best_time: dict[int, float] = {}
    for t, detections in scene_clusters.items():
        scene_totals[t] = sum(score for _, score in detections)
        # Use time of highest-scoring detection as the cut point
        best = max(detections, key=lambda x: x[1])
        scene_max[t] = best[1]
        scene_best_time[t] = best[0]

    black_map: dict[int, tuple[float, float]] = {}
    for end_time, duration in blacks:
        t = int(end_time)
        if t not in black_map or duration > black_map[t][1]:
            black_map[t] = (end_time, duration)

    # Build candidates from scene clusters (not from window expansion)
    candidates: list[CutCandidate] = []

    for t in scene_totals:
        cluster_total = scene_totals[t]
        max_score = scene_max[t]

        # Require at least one strong detection in the cluster
        # This filters clusters of many weak detections (false positives)
        # Threshold 8 separates true cuts (min 8.3) from false positives (max 7.7)
        if max_score < 8:
            continue

        # Look for corroborating signals in nearby seconds
        best_black_duration = 0.0
        best_audio_step = 0.0

        for offset in range(-window, window + 1):
            check_t = t + offset
            if check_t in black_map:
                _, duration = black_map[check_t]
                if duration > best_black_duration:
                    best_black_duration = duration
            if check_t in audio_changes and audio_changes[check_t] > best_audio_step:
                best_audio_step = audio_changes[check_t]

        # Only apply audio bonus if scene is strong (max >= 10)
        # For borderline detections, audio often indicates noise not confirmation
        effective_audio = best_audio_step if max_score >= 10 else 0.0

        candidate = CutCandidate(
            time=scene_best_time[t],
            scene_score=cluster_total,  # Use cluster total as score
            black_duration=best_black_duration,
            audio_step=effective_audio,
        )
        candidates.append(candidate)

    # Also add audio-only candidates (significant audio change without scene)
    for t, step in audio_changes.items():
        if step >= 15 and t not in scene_totals:
            candidate = CutCandidate(
                time=float(t),
                scene_score=0.0,
                black_duration=black_map.get(t, (0, 0))[1] if t in black_map else 0.0,
                audio_step=step,
            )
            candidates.append(candidate)

    # Sort by confidence score (highest first) for greedy selection
    sorted_candidates = sorted(candidates, key=lambda c: -c.confidence_score)
    selected = []

    for candidate in sorted_candidates:
        if candidate.confidence_score < min_confidence:
            continue

        too_close = False
        for existing in selected:
            if abs(candidate.time - existing.time) < min_gap:
                too_close = True
                break

        if not too_close:
            selected.append(candidate)

    result = sorted(selected, key=lambda c: c.time)
    if return_all:
        return result, candidates, scene_max, noise_zones
    return result


def detect_cuts(
    video_path: Path,
    start_time: float = 0,
    end_time: float | None = None,
    min_confidence: int = 12,
    min_gap: float = 1.0,
    verbose: bool = False,
    log_file=None,
) -> CutDetectionResult:
    """Run full cut detection pipeline: detect signals -> find cuts -> verify.

    Args:
        video_path: Path to video file
        start_time: Start time in seconds
        end_time: End time in seconds (None = full video)
        min_confidence: Minimum confidence score for cuts
        min_gap: Minimum gap between cuts in seconds
        verbose: Print verbose verification output
    """
    duration = get_video_duration(video_path)
    if end_time is None or end_time == 0:
        end_time = duration

    scenes = detect_scenes(video_path, start_time=start_time, end_time=end_time)
    blacks = detect_black_frames(video_path, start_time=start_time, end_time=end_time)
    audio_changes = detect_audio_changes(
        video_path, duration, start_time=start_time, end_time=end_time
    )

    cuts, all_candidates, scene_max, noise_zones = find_cuts(
        scenes,
        blacks,
        audio_changes,
        min_confidence=min_confidence,
        min_gap=min_gap,
        return_all=True,
    )

    verified = verify_candidates(
        video_path, cuts, scene_max, noise_zones=noise_zones, verbose=verbose, log_file=log_file
    )

    return CutDetectionResult(
        cuts=verified,
        all_candidates=all_candidates,
        scene_max_scores=scene_max,
        duration=duration,
        scenes=scenes,
        blacks=blacks,
        audio_changes=audio_changes,
        noise_zones=noise_zones,
    )
