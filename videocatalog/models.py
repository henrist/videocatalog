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


class UserEditsFile(BaseModel):
    """User edits for a video and its clips."""
    video: EditableMetadata = Field(default_factory=EditableMetadata)
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
