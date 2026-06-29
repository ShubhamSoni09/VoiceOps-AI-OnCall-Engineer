from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.agents import agents_router
from app.auth import auth_router
from app.collab import collab_router
from app.config import get_settings
from app.console import console_router
from app.external_agents import external_agents_router
from app.llm import llm_router
from app.long_memory import long_memory_router
from app.ontology import ontology_router
from app.speakers import speakers_router
from app.system import system_router
from app.system.security import validate_startup_security
from app.voice_agent.router import router as voice_router
from app.workspace.router import router as workspace_router

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
DESIGN_DIR = Path(__file__).resolve().parent.parent.parent / "design"
FRONTEND_DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

settings = get_settings()
validate_startup_security(settings)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield


app = FastAPI(
    title=settings.app_name,
    description="VoiceOps — AI On-Call Engineer. Investigate, fix, and deploy using voice.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(agents_router)
app.include_router(collab_router)
app.include_router(console_router)
app.include_router(external_agents_router)
app.include_router(llm_router)
app.include_router(long_memory_router)
app.include_router(ontology_router)
app.include_router(speakers_router)
app.include_router(system_router)
app.include_router(voice_router)
app.include_router(workspace_router)

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="react-assets")


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


@app.get("/react")
async def react_dashboard():
    """Built React app — prefer `npm run dev` in frontend/ for development."""
    index = FRONTEND_DIST / "index.html"
    if index.exists():
        return FileResponse(index)
    return {
        "error": "React build not found",
        "dev": "cd frontend && npm install && npm run dev  →  http://localhost:5191",
        "html_dashboard": "/dashboard",
    }


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
