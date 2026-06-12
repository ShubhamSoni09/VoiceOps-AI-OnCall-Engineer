import asyncio
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

settings = get_settings()


@asynccontextmanager
async def lifespan(_app: FastAPI):
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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
    return {
        "service": settings.app_name,
        "docs": "/docs",
        "login": "/login",
        "dashboard": "/dashboard",
    }


@app.get("/login")
async def login_page():
    page = DESIGN_DIR / "login.html"
    if page.exists():
        return FileResponse(page)
    return {"error": "Login page not found"}


@app.get("/talk")
async def talk():
    index = STATIC_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return {"error": "Talk UI not found"}


@app.get("/dashboard")
async def dashboard():
    page = DESIGN_DIR / "incident-dashboard.html"
    if page.exists():
        return FileResponse(page)
    return {"error": "Dashboard UI not found", "expected": str(page)}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
