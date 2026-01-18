"""HTML gallery generation."""

import html as html_lib
import importlib.resources
import json
from collections.abc import Callable
from pathlib import Path

from jinja2 import BaseLoader, Environment

from .models import UserEditsFile, VideoMetadata


def _parse_duration_secs(duration_str: str) -> int:
    """Parse duration string like '1:23' or '1:02:34' to seconds."""
    parts = duration_str.split(":")
    if len(parts) == 2:
        return int(parts[0]) * 60 + int(parts[1])
    elif len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return 0


def _load_template_file(name: str) -> str:
    """Load a template file from the templates directory."""
    return importlib.resources.files(__package__).joinpath("templates", name).read_text()


def generate_gallery(
    output_dir: Path, transcribe: bool = True, log: Callable[[str], None] = print
) -> None:
    """Generate HTML gallery from all processed videos in subdirectories."""
    log("Generating gallery...")

    sources = []
    all_user_edits = {}

    for subdir in sorted(output_dir.iterdir()):
        if not subdir.is_dir():
            continue
        metadata_path = subdir / "metadata.json"
        if not metadata_path.exists():
            continue

        metadata = VideoMetadata.load(metadata_path)

        # Build clip data with escaped transcripts
        clips_data = []
        for clip in metadata.clips:
            clips_data.append(
                {
                    "name": clip.name,
                    "file": clip.file,
                    "sprite": clip.sprite,
                    "thumbs": clip.thumbs,
                    "duration": clip.duration,
                    "duration_secs": _parse_duration_secs(clip.duration),
                    "transcript": html_lib.escape(clip.transcript),
                }
            )

        sources.append({"name": subdir.name, "clips": clips_data})
        print(f"  Found {len(metadata.clips)} clips in {subdir.name}")

        # Load user edits if exists
        user_edits_path = subdir / "user_edits.json"
        if user_edits_path.exists():
            edits = UserEditsFile.load(user_edits_path)
            all_user_edits[subdir.name] = edits.model_dump()

    if not sources:
        print("  No processed videos found")
        return

    total_clips = sum(len(s["clips"]) for s in sources)
    print(f"  Total: {total_clips} clips from {len(sources)} videos")

    # Load template files
    css = _load_template_file("gallery.css")
    js = _load_template_file("gallery.js")
    template_str = _load_template_file("gallery.html.j2")

    # Escape </script> to prevent XSS when embedding in HTML
    user_edits_json = json.dumps(all_user_edits).replace("</script>", "<\\/script>")

    # Render template
    env = Environment(loader=BaseLoader(), autoescape=False)
    template = env.from_string(template_str)
    html = template.render(
        css=css,
        js=js,
        sources=sources,
        user_edits_json=user_edits_json,
    )

    gallery_path = output_dir / "gallery.html"
    gallery_path.write_text(html)
    print(f"  Gallery: {gallery_path}")
