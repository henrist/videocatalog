"""FastAPI server for editing video metadata."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .models import UserEditsFile
from .gallery import generate_gallery

app = FastAPI(title="Video Catalog")


@app.get("/")
async def index():
    """Serve the gallery HTML, optionally regenerating first."""
    if not hasattr(app.state, 'output_dir'):
        raise HTTPException(500, "Server not configured")
    if getattr(app.state, 'regenerate', False):
        generate_gallery(app.state.output_dir)
    gallery_path = app.state.output_dir / "gallery.html"
    if not gallery_path.exists():
        raise HTTPException(404, "Gallery not found")
    return FileResponse(gallery_path)


def _get_video_dir(video_name: str) -> Path:
    """Get video directory, validating it exists and is within output_dir."""
    if not hasattr(app.state, 'output_dir'):
        raise HTTPException(500, "Server not configured")

    output_dir = app.state.output_dir.resolve()
    video_dir = (output_dir / video_name).resolve()
    if not video_dir.is_relative_to(output_dir):
        raise HTTPException(400, "Invalid video name")
    if not video_dir.is_dir():
        raise HTTPException(404, f"Video not found: {video_name}")
    return video_dir


@app.get("/api/edits/{video_name}")
async def get_edits(video_name: str) -> dict:
    """Get user edits for a video."""
    video_dir = _get_video_dir(video_name)

    edits_path = video_dir / "user_edits.json"
    if edits_path.exists():
        return UserEditsFile.load(edits_path).model_dump()
    return UserEditsFile().model_dump()


@app.put("/api/edits/{video_name}")
async def save_edits(video_name: str, edits: UserEditsFile) -> dict:
    """Save user edits for a video and regenerate gallery."""
    video_dir = _get_video_dir(video_name)

    edits_path = video_dir / "user_edits.json"
    edits.save(edits_path)

    generate_gallery(app.state.output_dir)

    return {"status": "ok"}


def create_app(directory: Path, regenerate: bool = False) -> FastAPI:
    """Create app with static files mounted."""
    app.state.output_dir = directory.resolve()
    app.state.regenerate = regenerate

    # Track mounted routes to avoid duplicates on repeated calls
    mounted = getattr(app.state, 'mounted_routes', set())

    # Mount static files for video subdirs (must be after API routes)
    for subdir in app.state.output_dir.iterdir():
        if subdir.is_dir() and subdir.name not in mounted:
            app.mount(f"/{subdir.name}", StaticFiles(directory=subdir), name=subdir.name)
            mounted.add(subdir.name)

    app.state.mounted_routes = mounted
    return app


def run_server(directory: Path, host: str = "127.0.0.1", port: int = 8000, regenerate: bool = False):
    """Run the server."""
    import uvicorn

    create_app(directory, regenerate)

    print(f"Serving gallery at http://{host}:{port}")
    if regenerate:
        print("Gallery will regenerate on each page load")
    uvicorn.run(app, host=host, port=port)
