from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.auth import auth_router
from app.config import get_settings
from app.console import console_router
from app.voice_agent.router import router as voice_router

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
DESIGN_DIR = Path(__file__).resolve().parent.parent.parent / "design"
FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    import asyncio
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if settings.stt_provider == "whisper":
        from app.voice_agent.stt.whisper import WhisperSTT
        stt = WhisperSTT(settings)
        await asyncio.to_thread(stt.preload)
    yield


app = FastAPI(
    title=settings.app_name,
    description="VoiceOps — AI On-Call Engineer. Investigate, fix, and deploy using voice.",
    version="0.1.0",
    lifespan=lifespan,
)

_raw_origins = settings.allowed_origins or ""
_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(console_router)
app.include_router(voice_router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def root():
    login = DESIGN_DIR / "login.html"
    if login.exists():
        return FileResponse(login)
    return {"service": settings.app_name, "docs": "/docs"}


@app.get("/react")
async def react_dashboard():
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"error": "React build not found", "dev": "cd frontend && npm run dev"}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
