"""FastAPI server for editing video metadata."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .models import UserEditsFile
from .gallery import generate_gallery

app = FastAPI(title="Video Catalog")

# Will be set by run_server()
output_dir: Path | None = None


@app.get("/")
async def index():
    """Serve the gallery HTML."""
    if not output_dir:
        raise HTTPException(500, "Server not configured")
    gallery_path = output_dir / "gallery.html"
    if not gallery_path.exists():
        raise HTTPException(404, "Gallery not found")
    return FileResponse(gallery_path)


@app.get("/api/edits/{video_name}")
async def get_edits(video_name: str) -> dict:
    """Get user edits for a video."""
    if not output_dir:
        raise HTTPException(500, "Server not configured")

    video_dir = output_dir / video_name
    if not video_dir.is_dir():
        raise HTTPException(404, f"Video not found: {video_name}")

    edits_path = video_dir / "user_edits.json"
    if edits_path.exists():
        return UserEditsFile.load(edits_path).model_dump()
    return UserEditsFile().model_dump()


@app.put("/api/edits/{video_name}")
async def save_edits(video_name: str, edits: UserEditsFile) -> dict:
    """Save user edits for a video and regenerate gallery."""
    if not output_dir:
        raise HTTPException(500, "Server not configured")

    video_dir = output_dir / video_name
    if not video_dir.is_dir():
        raise HTTPException(404, f"Video not found: {video_name}")

    edits_path = video_dir / "user_edits.json"
    edits.save(edits_path)

    # Regenerate gallery
    generate_gallery(output_dir)

    return {"status": "ok"}


def create_app(directory: Path) -> FastAPI:
    """Create app with static files mounted."""
    global output_dir
    output_dir = directory.resolve()

    # Mount static files for video subdirs (must be after API routes)
    for subdir in output_dir.iterdir():
        if subdir.is_dir():
            app.mount(f"/{subdir.name}", StaticFiles(directory=subdir), name=subdir.name)

    return app


def run_server(directory: Path, host: str = "127.0.0.1", port: int = 8000):
    """Run the server."""
    import uvicorn

    create_app(directory)

    print(f"Serving gallery at http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)
