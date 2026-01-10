"""Data models for videocatalog."""

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class ClipInfo:
    """Metadata for a single video clip."""
    file: str
    name: str
    thumbs: list[str]
    duration: str
    transcript: str


@dataclass
class VideoMetadata:
    """Metadata for a processed source video."""
    source_file: str
    processed_date: str
    clips: list[ClipInfo] = field(default_factory=list)

    def save(self, path: Path) -> None:
        data = {
            'source_file': self.source_file,
            'processed_date': self.processed_date,
            'clips': [asdict(c) for c in self.clips]
        }
        path.write_text(json.dumps(data, indent=2))

    @classmethod
    def load(cls, path: Path) -> 'VideoMetadata':
        data = json.loads(path.read_text())
        clips = [ClipInfo(**c) for c in data.get('clips', [])]
        return cls(
            source_file=data['source_file'],
            processed_date=data['processed_date'],
            clips=clips
        )


@dataclass
class CatalogEntry:
    """Entry in the master catalog."""
    name: str
    source_file: str
    processed_date: str
    clip_count: int


@dataclass
class CutCandidate:
    """A potential cut point with confidence scoring."""
    time: float
    scene_score: float = 0.0
    black_duration: float = 0.0
    audio_step: float = 0.0

    @property
    def confidence_score(self) -> int:
        """Calculate confidence score based on multiple signals."""
        score = 0

        # Scene detection scoring
        if self.scene_score >= 25:
            score += 40
        elif self.scene_score >= 15:
            score += 25
        elif self.scene_score >= 10:
            score += 15
        elif self.scene_score >= 6:
            score += 5

        # Black frame scoring
        if self.black_duration >= 1.0:
            score += 35
        elif self.black_duration >= 0.5:
            score += 25
        elif self.black_duration >= 0.2:
            score += 15

        # Audio RMS step scoring
        if self.audio_step >= 25:
            score += 30
        elif self.audio_step >= 18:
            score += 20
        elif self.audio_step >= 12:
            score += 10

        return score

    def signal_summary(self) -> str:
        parts = []
        if self.scene_score > 0:
            parts.append(f"scene:{self.scene_score:.1f}")
        if self.black_duration > 0:
            parts.append(f"black:{self.black_duration:.2f}s")
        if self.audio_step > 0:
            parts.append(f"audio:{self.audio_step:.1f}dB")
        return " ".join(parts) if parts else "none"
