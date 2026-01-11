"""Data models for videocatalog."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, computed_field


class ClipInfo(BaseModel):
    """Metadata for a single video clip."""
    file: str
    name: str
    thumbs: list[str]
    duration: str
    transcript: str


class VideoMetadata(BaseModel):
    """Metadata for a processed source video."""
    source_file: str
    processed_date: str
    clips: list[ClipInfo] = Field(default_factory=list)

    def save(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls, path: Path) -> 'VideoMetadata':
        return cls.model_validate_json(path.read_text())


class CatalogEntry(BaseModel):
    """Entry in the master catalog."""
    name: str
    source_file: str
    processed_date: str
    clip_count: int


ConfidenceLevel = Literal["high", "medium", "low"]


class TagInfo(BaseModel):
    """A tag with confidence level."""
    name: str
    confidence: ConfidenceLevel = "high"


class YearInfo(BaseModel):
    """Year estimate with confidence level."""
    year: int
    confidence: ConfidenceLevel = "low"


class EditableMetadata(BaseModel):
    """User-editable metadata for a video or clip."""
    tags: list[TagInfo] = Field(default_factory=list)
    year: YearInfo | None = None
    description: str | None = None


class ClipGroup(BaseModel):
    """A group of successive clips with shared metadata."""
    id: str
    start_clip: str
    end_clip: str
    tags: list[TagInfo] = Field(default_factory=list)
    year: YearInfo | None = None
    description: str | None = None


class UserEditsFile(BaseModel):
    """User edits for a video and its clips."""
    video: EditableMetadata = Field(default_factory=EditableMetadata)
    groups: list[ClipGroup] = Field(default_factory=list)
    clips: dict[str, EditableMetadata] = Field(default_factory=dict)

    def save(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls, path: Path) -> 'UserEditsFile':
        return cls.model_validate_json(path.read_text())


class CutCandidate(BaseModel):
    """A potential cut point with confidence scoring."""
    time: float
    scene_score: float = 0.0
    black_duration: float = 0.0
    audio_step: float = 0.0

    @computed_field
    @property
    def confidence_score(self) -> int:
        """Calculate confidence score: raw scene score + bonuses for black/audio."""
        score = int(self.scene_score)

        # Bonus for corroborating black frames
        if self.black_duration >= 0.2:
            score += 10

        # Bonus for corroborating audio change
        if self.audio_step >= 5:
            score += 10

        return score

    def score_breakdown(self) -> tuple[int, int, int]:
        """Return (scene_pts, black_pts, audio_pts) score components."""
        scene_pts = int(self.scene_score)
        black_pts = 10 if self.black_duration >= 0.2 else 0
        audio_pts = 10 if self.audio_step >= 5 else 0
        return scene_pts, black_pts, audio_pts

    def signal_summary(self) -> str:
        parts = []
        if self.scene_score > 0:
            parts.append(f"scene:{self.scene_score:.1f}")
        if self.black_duration > 0:
            parts.append(f"black:{self.black_duration:.2f}s")
        if self.audio_step > 0:
            parts.append(f"audio:{self.audio_step:.1f}dB")
        return " ".join(parts) if parts else "none"


# Split detection data models

class SceneDetection(BaseModel):
    """A detected scene change."""
    time: float
    score: float


class BlackDetection(BaseModel):
    """A detected black frame sequence."""
    end_time: float
    duration: float


class AudioChange(BaseModel):
    """A detected audio level change."""
    time: int
    step: float


class DetectionData(BaseModel):
    """All raw detection signals."""
    scenes: list[SceneDetection] = Field(default_factory=list)
    blacks: list[BlackDetection] = Field(default_factory=list)
    audio_changes: list[AudioChange] = Field(default_factory=list)


class CandidateInfo(BaseModel):
    """A cut candidate with selection status."""
    time: float
    scene_score: float = 0.0
    black_duration: float = 0.0
    audio_step: float = 0.0
    confidence_score: int
    selected: bool


class SegmentInfo(BaseModel):
    """A video segment created by splitting."""
    index: int
    start: float
    end: float
    output_file: str


class SplitParameters(BaseModel):
    """Parameters used for split detection."""
    min_confidence: int
    min_gap: float


class SplitsFile(BaseModel):
    """Complete split detection data for a video."""
    source_file: str
    duration: float
    processed_date: str
    parameters: SplitParameters
    detection: DetectionData
    candidates: list[CandidateInfo]
    segments: list[SegmentInfo]

    def save(self, path: Path) -> None:
        path.write_text(self.model_dump_json(indent=2))

    @classmethod
    def load(cls, path: Path) -> 'SplitsFile':
        return cls.model_validate_json(path.read_text())
