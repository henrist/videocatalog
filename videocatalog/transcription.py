"""Whisper transcription for video clips."""

import os
import subprocess
from pathlib import Path

from .utils import has_content


_whisper_model = None


def get_whisper_model():
    """Get or create the Whisper model (singleton per process)."""
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        print(f"  [pid {os.getpid()}] Loading Whisper large-v3 model...")
        _whisper_model = WhisperModel("large-v3", device="auto", compute_type="auto")
    return _whisper_model


def extract_audio(video_path: Path) -> Path:
    """Extract audio from video to WAV for cleaner transcription."""
    wav_path = video_path.with_suffix('.wav')
    if wav_path.exists():
        return wav_path

    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "pcm_s16le",
        "-ar", "16000",
        "-ac", "1",
        str(wav_path)
    ]
    subprocess.run(cmd, capture_output=True)
    return wav_path


def _transcribe_wav(wav_path: Path) -> str:
    """Run Whisper transcription on a WAV file."""
    model = get_whisper_model()
    segments, _ = model.transcribe(
        str(wav_path),
        language="no",
        beam_size=10,
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500}
    )
    return " ".join(seg.text.strip() for seg in segments)


def transcribe_from_wav(video_path: Path, wav_path: Path) -> str:
    """Transcribe from pre-extracted WAV file. Deletes WAV when done."""
    txt_path = video_path.with_suffix('.txt')

    if has_content(txt_path):
        wav_path.unlink(missing_ok=True)
        return txt_path.read_text()

    try:
        text = _transcribe_wav(wav_path)
        txt_path.write_text(text)
        return text
    except Exception as e:
        print(f"    Error transcribing {video_path.name}: {e}")
        return ""
    finally:
        wav_path.unlink(missing_ok=True)


def transcribe_worker(args: tuple[str, str]) -> tuple[str, str]:
    """Worker function for multiprocessing pool. Takes/returns strings for pickling."""
    video_path_str, wav_path_str = args
    transcript = transcribe_from_wav(Path(video_path_str), Path(wav_path_str))
    return (video_path_str, transcript)
