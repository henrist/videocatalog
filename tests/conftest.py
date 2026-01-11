"""Pytest configuration for scene split regression tests."""

import json
import os
from pathlib import Path

import pytest

TEST_VIDEOS_DIR = os.environ.get("VIDEOCATALOG_TEST_VIDEOS", os.getcwd())
TEST_LIMIT = float(os.environ.get("VIDEOCATALOG_TEST_LIMIT", 0))  # Max seconds to analyze (0=full)


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line("markers", "requires_videos: test requires video files")


@pytest.fixture
def test_videos_dir():
    """Return path to test videos directory, skip if not found."""
    path = Path(TEST_VIDEOS_DIR)
    if not path.exists():
        pytest.skip(f"Test videos directory not found: {path}")
    return path


def load_golden_files():
    """Load all golden files from tests/golden/."""
    golden_dir = Path(__file__).parent / "golden"
    if not golden_dir.exists():
        return []

    golden_files = []
    for f in sorted(golden_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text())
            data["_file"] = f.name
            golden_files.append(data)
        except json.JSONDecodeError as e:
            print(f"Warning: Failed to parse {f}: {e}")
    return golden_files


def pytest_generate_tests(metafunc):
    """Parametrize tests with golden files."""
    if "golden" in metafunc.fixturenames:
        golden_files = load_golden_files()
        ids = [g.get("_file", f"golden_{i}") for i, g in enumerate(golden_files)]
        metafunc.parametrize("golden", golden_files, ids=ids)
